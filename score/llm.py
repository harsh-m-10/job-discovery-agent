"""LAYER 2, stage 2 — Groq batch scoring.

The prompt is assembled from config/candidate_profile.yaml so that resume facts
and calibration rules live in one operator-editable file, never in code. The
`scoring_rules` block is injected verbatim: it is the only lever that controls
score inflation, and paraphrasing it in code would let the two drift apart.

Nothing here may generate compensation, notice period, or any other factual
personal field. Those are read from the profile and never pass through a model.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "score" / "prompts" / "scoring.txt"
PROFILE_FILE = ROOT / "config" / "candidate_profile.yaml"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Chosen for its rate limit, not its leaderboard position. Groq's free tier caps
# tokens-per-minute per model, and that ceiling — not quality — is the binding
# constraint here: gpt-oss-120b and qwen3.6-27b allow 8,000 TPM, llama-3.1-8b
# only 6,000, while llama-3.3-70b-versatile allows 12,000. The TPM budget counts
# requested output tokens too, so a batch of 8 full-length JDs cannot fit on any
# of them.
DEFAULT_MODEL = "llama-3.3-70b-versatile"

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I | re.M)


class ScoringError(RuntimeError):
    pass


class RequestTooLarge(ScoringError):
    """The batch cannot fit the per-minute token budget; split it."""


class RateLimited(ScoringError):
    """Per-minute budget spent. Carries how long the API asked us to wait."""

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(f"rate limited (retry in {retry_after:.0f}s): {message}")
        self.retry_after = retry_after


class DailyQuotaExhausted(ScoringError):
    """The per-day token budget is gone (TPD), not the per-minute one.

    Distinct from RateLimited because the response is different in kind: no
    amount of waiting inside a run recovers it, and retrying only burns whatever
    budget is left. The run must stop and resume after the quota resets.
    """

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(f"daily token quota exhausted: {message}")
        self.retry_after = retry_after


# "Please try again in 33m40.032s" — the only trustworthy delay for a daily
# limit. The x-ratelimit-reset-tokens header describes the per-minute window and
# reads as milliseconds here, which is what made the first implementation retry
# immediately and burn the rest of the day's budget.
RETRY_HINT = re.compile(
    r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", re.I)


def _retry_hint_seconds(body: str) -> float:
    match = RETRY_HINT.search(body or "")
    if not match:
        return 0.0
    minutes = float(match.group(1) or 0)
    return minutes * 60 + float(match.group(2))


class _Pacer:
    """Keeps request rate inside the free-tier TPM ceiling.

    Groq reports remaining tokens and a reset delay on every response. Reading
    those is far more reliable than guessing a sleep interval, because the
    budget is consumed by input, output, and other callers on the same key.
    """

    def __init__(self) -> None:
        self.remaining: float | None = None
        self.reset_after: float = 0.0

    @staticmethod
    def _seconds(value: str | None) -> float:
        if not value:
            return 0.0
        match = re.match(r"^([\d.]+)\s*(ms|m|s)?$", value.strip())
        if not match:
            return 0.0
        amount = float(match.group(1))
        unit = match.group(2) or "s"
        return amount / 1000 if unit == "ms" else amount * 60 if unit == "m" else amount

    def observe(self, headers) -> None:
        try:
            self.remaining = float(headers.get("x-ratelimit-remaining-tokens", ""))
        except ValueError:
            self.remaining = None
        self.reset_after = self._seconds(headers.get("x-ratelimit-reset-tokens"))

    def wait_for(self, needed: int) -> None:
        if self.remaining is not None and self.remaining < needed:
            delay = min(max(self.reset_after, 1.0) + 1.0, 65.0)
            print(f"    (token budget low: {self.remaining:.0f} left, "
                  f"need ~{needed} — waiting {delay:.0f}s)")
            time.sleep(delay)
            self.remaining = None


_pacer = _Pacer()


def load_profile() -> dict:
    with PROFILE_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_system_prompt(profile: dict | None = None) -> str:
    """Assemble the system prompt.

    The compensation block is deliberately excluded: the model never needs it,
    and a hallucinated CTC figure is unrecoverable once it reaches an employer.
    """
    profile = profile or load_profile()
    facts = {k: v for k, v in profile.items()
             if k not in ("scoring_rules", "compensation")}
    rules = profile.get("scoring_rules", {})

    facts_text = yaml.safe_dump(facts, sort_keys=False, allow_unicode=True,
                                default_flow_style=False, width=100)
    rules_text = "\n".join(
        f"- {key.replace('_', ' ').upper()}: {str(value).strip()}"
        for key, value in rules.items()
    )
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return (template
            .replace("{CANDIDATE_FACTS}", facts_text.strip())
            .replace("{SCORING_RULES}", rules_text.strip())
            .replace("{{", "{").replace("}}", "}"))


def build_user_message(jobs: list[dict], char_limit: int) -> str:
    """One compact block per job. Head+tail truncation happens upstream."""
    from ingest.normalize import truncate_for_llm

    blocks = []
    for job in jobs:
        company = (job.get("companies") or {}).get("name") or "Unknown"
        category = (job.get("companies") or {}).get("category") or ""
        blocks.append(
            f"### job_id: {job['id']}\n"
            f"Company: {company}{f' (category: {category})' if category else ''}\n"
            f"Title: {job['title']}\n"
            f"Location: {job.get('location') or 'unstated'}\n"
            f"Job description:\n"
            f"{truncate_for_llm(job.get('description') or '', char_limit)}"
        )
    return (f"Score these {len(jobs)} jobs. Return one entry per job_id, "
            f"in this order.\n\n" + "\n\n---\n\n".join(blocks))


def _call_groq(system: str, user: str, model: str, max_output: int,
               timeout: int = 180) -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise ScoringError("GROQ_API_KEY is not set")

    # Rough but adequate: the budget check only needs to be right to within a
    # few hundred tokens, and requested output counts against TPM as well.
    estimated = (len(system) + len(user)) // 4 + max_output
    _pacer.wait_for(estimated)

    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,          # scoring should be near-deterministic
            "max_tokens": max_output,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    _pacer.observe(resp.headers)

    if resp.status_code == 413:
        raise RequestTooLarge(resp.text[:200])
    if resp.status_code == 429:
        body = resp.text[:300]
        # A 429 whose message is about request size will never succeed on retry.
        if "Request too large" in body:
            raise RequestTooLarge(body)
        hint = _retry_hint_seconds(body)
        if "tokens per day" in body.lower() or "(tpd)" in body.lower():
            raise DailyQuotaExhausted(hint, body)
        # Otherwise the per-minute budget is spent. Groq says how long to wait;
        # sleeping that long beats failing the batch, since a failed batch costs
        # a re-run over the whole queue.
        delay = hint or _Pacer._seconds(resp.headers.get("retry-after")) or \
            _Pacer._seconds(resp.headers.get("x-ratelimit-reset-tokens")) or 30.0
        raise RateLimited(min(delay + 2.0, 90.0), body)
    if resp.status_code >= 300:
        raise ScoringError(f"groq {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def parse_scores(text: str) -> list[dict]:
    """Strip fences defensively even though JSON mode is requested — the
    instruction not to emit them is not a guarantee."""
    cleaned = FENCE.sub("", text or "").strip()
    if not cleaned:
        raise ScoringError("empty response")
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Last resort: pull the outermost object out of surrounding prose.
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ScoringError(f"unparseable response: {cleaned[:200]}") from exc
        payload = json.loads(match.group(0))

    if isinstance(payload, list):
        return payload
    for key in ("scores", "results", "jobs"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ScoringError(f"no score array in response: {cleaned[:200]}")


VERDICTS = ("strong", "worth_trying", "stretch", "reject")


def _clean_number(value, low=0.0, high=10.0) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, num))


def normalize_entry(entry: dict, model: str) -> dict | None:
    """Coerce one model entry into a job_scores row, or None if unusable.

    The verdict is recomputed from fit_score rather than trusted: models
    routinely return a generous verdict beside a modest number, and the ping
    threshold keys off the score.
    """
    job_id = entry.get("job_id")
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        return None

    score = _clean_number(entry.get("fit_score"))
    if score is None:
        return None

    verdict = ("strong" if score >= 8.0 else
               "worth_trying" if score >= 6.0 else
               "stretch" if score >= 4.0 else "reject")

    def string_list(value) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:8]

    reasoning = str(entry.get("reasoning") or "").strip()
    return {
        "job_id": job_id,
        "fit_score": round(score, 1),
        "verdict": verdict,
        "min_years": _clean_number(entry.get("min_years"), 0.0, 50.0),
        "max_years": _clean_number(entry.get("max_years"), 0.0, 50.0),
        "matched_skills": string_list(entry.get("matched_skills")),
        "gap_skills": string_list(entry.get("gap_skills")),
        "reasoning": reasoning[:600],
        "model": model,
    }


#: Output allowance per job. Entries run ~150 tokens; this leaves headroom
#: without wasting TPM budget, which counts requested output against the cap.
OUTPUT_TOKENS_PER_JOB = 320


def score_batch(jobs: list[dict], *, model: str = DEFAULT_MODEL,
                char_limit: int = 6000, system: str | None = None) -> list[dict]:
    """Score one batch. Retries once on malformed JSON, then gives up on the
    batch — a single bad response must never abort a run."""
    system = system or build_system_prompt()
    user = build_user_message(jobs, char_limit)
    wanted = {j["id"] for j in jobs}
    max_output = min(4096, OUTPUT_TOKENS_PER_JOB * len(jobs) + 200)

    last_error = ""
    for attempt in range(3):
        try:
            raw = _call_groq(system, user, model, max_output)
            entries = parse_scores(raw)
        except DailyQuotaExhausted:
            # Propagate: this batch was never attempted in any meaningful sense,
            # and marking its jobs failed would hide them from the next run.
            raise
        except RateLimited as exc:
            last_error = str(exc)
            if attempt < 2:
                print(f"    (rate limited — waiting {exc.retry_after:.0f}s)")
                time.sleep(exc.retry_after)
            continue
        except RequestTooLarge as exc:
            # Halve and recurse: one oversized JD must not cost the whole batch.
            if len(jobs) > 1:
                mid = len(jobs) // 2
                return (score_batch(jobs[:mid], model=model, char_limit=char_limit,
                                    system=system)
                        + score_batch(jobs[mid:], model=model, char_limit=char_limit,
                                      system=system))
            # A single job that still will not fit: shrink its description.
            if char_limit > 1200:
                return score_batch(jobs, model=model, char_limit=char_limit // 2,
                                   system=system)
            last_error = f"request too large: {exc}"
            break
        except ScoringError as exc:
            last_error = str(exc)
            continue

        rows = [r for r in (normalize_entry(e, model) for e in entries) if r]
        rows = [r for r in rows if r["job_id"] in wanted]
        if rows:
            missing = wanted - {r["job_id"] for r in rows}
            for job_id in missing:
                rows.append({"job_id": job_id, "fit_score": None, "verdict": "reject",
                             "reject_reason": "scoring_failed:omitted_by_model",
                             "model": model})
            return rows
        last_error = "no usable entries"

    return [{"job_id": job_id, "fit_score": None, "verdict": "reject",
             "reject_reason": f"scoring_failed:{last_error[:80]}", "model": model}
            for job_id in wanted]
