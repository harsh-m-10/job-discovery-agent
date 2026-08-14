"""LAYER 2, stage 2 — batch scoring across multiple LLM providers.

The prompt is assembled from config/candidate_profile.yaml so that resume facts
and calibration rules live in one operator-editable file, never in code. The
`scoring_rules` block is injected verbatim: it is the only lever that controls
score inflation, and paraphrasing it in code would let the two drift apart.

Nothing here may generate compensation, notice period, or any other factual
personal field. `build_system_prompt` strips the whole `compensation` block, and
`tests/test_llm.py` asserts it — a hallucinated CTC figure is unrecoverable once
an employer has seen it.

Provider selection and failover live in score/providers.py; this module owns the
prompt, the batching, and turning a model's reply into a job_scores row.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from .providers import (ChatProvider, NotConfigured, PaymentRequired,
                        ProviderError, QuotaExhausted, RateLimited,
                        RequestTooLarge, ServerError, build_providers)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "score" / "prompts" / "scoring.txt"
PROFILE_FILE = ROOT / "config" / "candidate_profile.yaml"

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I | re.M)

#: Output allowance per job. Entries run ~150 tokens; this leaves headroom
#: without wasting budget, since requested output counts against most caps.
OUTPUT_TOKENS_PER_JOB = 320


class ScoringError(RuntimeError):
    pass


class AllProvidersExhausted(ScoringError):
    """Every configured provider refused. Callers must stop, not retry."""


def load_profile() -> dict:
    with PROFILE_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_system_prompt(profile: dict | None = None) -> str:
    """Assemble the system prompt.

    The compensation block is excluded deliberately: the model never needs it,
    and a hallucinated CTC or notice period is unrecoverable.
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
        band = (job.get("companies") or {}).get("headcount_band") or "unknown"
        blocks.append(
            f"### job_id: {job['id']}\n"
            f"Company: {company}{f' (category: {category})' if category else ''}\n"
            f"Company size band: {band}\n"
            f"Title: {job['title']}\n"
            f"Location: {job.get('location') or 'unstated'}\n"
            f"Job description:\n"
            f"{truncate_for_llm(job.get('description') or '', char_limit)}"
        )
    return (f"Score these {len(jobs)} jobs. Return one entry per job_id, "
            f"in this order.\n\n" + "\n\n---\n\n".join(blocks))


def parse_scores(text: str) -> list[dict]:
    """Strip fences defensively even though JSON mode is requested — the
    instruction not to emit them is not a guarantee, and it varies by vendor."""
    cleaned = FENCE.sub("", text or "").strip()
    if not cleaned:
        raise ScoringError("empty response")
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ScoringError(f"unparseable response: {cleaned[:200]}") from exc
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as inner:
            # Usually a reply truncated by max_tokens mid-object. Surfacing it
            # as ScoringError lets _attempt retry instead of killing the run.
            raise ScoringError(
                f"truncated or malformed JSON ({inner}): {cleaned[:160]}"
            ) from inner

    if isinstance(payload, list):
        return payload
    for key in ("scores", "results", "jobs"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ScoringError(f"no score array in response: {cleaned[:200]}")


def _clean_number(value, low=0.0, high=10.0) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, num))


def verdict_for(score: float) -> str:
    return ("strong" if score >= 8.0 else
            "worth_trying" if score >= 6.0 else
            "stretch" if score >= 4.0 else "reject")


def apply_headcount_penalty(row: dict, band: str | None,
                            penalties: dict[str, float]) -> dict:
    """Adjust a scored row for company size, then re-derive the verdict.

    Applied in code rather than delegated to the prompt: the band is a hard fact
    from our own database, not something the model should be re-judging, and a
    deterministic adjustment is auditable — `fit_score` moves by exactly the
    configured amount and nothing else changes.
    """
    if row.get("fit_score") is None:
        return row
    delta = float(penalties.get((band or "unknown").lower(), 0.0))
    if not delta:
        return row
    adjusted = max(0.0, min(10.0, float(row["fit_score"]) + delta))
    row["fit_score"] = round(adjusted, 1)
    row["verdict"] = verdict_for(adjusted)
    return row


def normalize_entry(entry: dict, model: str, provider: str = "") -> dict | None:
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

    verdict = verdict_for(score)

    def string_list(value) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:8]

    return {
        "job_id": job_id,
        "fit_score": round(score, 1),
        "verdict": verdict,
        "min_years": _clean_number(entry.get("min_years"), 0.0, 50.0),
        "max_years": _clean_number(entry.get("max_years"), 0.0, 50.0),
        "matched_skills": string_list(entry.get("matched_skills")),
        "gap_skills": string_list(entry.get("gap_skills")),
        "reasoning": str(entry.get("reasoning") or "").strip()[:600],
        # Explicitly cleared. PostgREST updates only the columns present in the
        # payload, so omitting this left a stale "scoring_failed:..." marker on
        # a row that had just been scored successfully — which made the
        # dashboard show a permanent error banner and made --retry-failed
        # re-score jobs that were already fine.
        "reject_reason": None,
        "model": model,
        "provider": provider,
    }


def _attempt(provider: ChatProvider, jobs: list[dict], system: str,
             char_limit: int, attempts: int = 3) -> list[dict]:
    """Score `jobs` on one provider, or raise so the caller falls through.

    Handles the three recoverable-in-place conditions: an oversized request is
    split, a per-minute limit is waited out, and a malformed reply is retried.
    A quota exhaustion is *not* recoverable here and propagates immediately.
    """
    wanted = {j["id"] for j in jobs}
    max_output = min(4096, provider.cfg.max_output_tokens_per_job * len(jobs) + 200)
    user = build_user_message(jobs, char_limit)
    last_error = ""

    for attempt in range(attempts):
        try:
            raw = provider.complete(system, user, max_output)
            entries = parse_scores(raw)
        except RequestTooLarge as exc:
            if len(jobs) > 1:
                mid = len(jobs) // 2
                return (_attempt(provider, jobs[:mid], system, char_limit)
                        + _attempt(provider, jobs[mid:], system, char_limit))
            if char_limit > 1000:
                return _attempt(provider, jobs, system, char_limit // 2)
            raise
        except RateLimited as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                # Exponential backoff on top of the vendor's own hint.
                delay = min(exc.retry_after * (2 ** attempt), 90.0)
                print(f"    [{provider.name}] rate limited — waiting {delay:.0f}s")
                time.sleep(delay)
                continue
            raise
        except ServerError as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                delay = min(4.0 * (2 ** attempt), 45.0)
                print(f"    [{provider.name}] {str(exc)[:60]} — retrying in {delay:.0f}s")
                time.sleep(delay)
                continue
            raise
        except ScoringError as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise ProviderError(f"{provider.name}: {last_error}") from exc

        rows = [r for r in (normalize_entry(e, provider.cfg.model, provider.name)
                            for e in entries) if r]
        rows = [r for r in rows if r["job_id"] in wanted]
        if not rows:
            last_error = "no usable entries"
            if attempt < attempts - 1:
                continue
            raise ProviderError(f"{provider.name}: {last_error}")

        for job_id in wanted - {r["job_id"] for r in rows}:
            rows.append({"job_id": job_id, "fit_score": None, "verdict": "reject",
                         "reject_reason": "scoring_failed:omitted_by_model",
                         "model": provider.cfg.model, "provider": provider.name})
        return rows

    raise ProviderError(f"{provider.name}: {last_error}")


def score_batch(jobs: list[dict], providers: list[ChatProvider], *,
                system: str | None = None) -> list[dict]:
    """Score one batch, falling through providers until one succeeds.

    Batch size and character limit are per-provider, so a fallback with a
    tighter ceiling re-chunks rather than failing. Raises
    AllProvidersExhausted only when every configured vendor has refused, which
    the caller must treat as "stop the run", not "mark these jobs failed".
    """
    system = system or build_system_prompt()
    usable = [p for p in providers if p.available()]
    if not usable:
        raise AllProvidersExhausted(
            "no provider has an API key set — expected one of: "
            + ", ".join(p.cfg.api_key_env for p in providers)
        )

    failures: list[str] = []
    for provider in usable:
        try:
            return _attempt(provider, jobs, system, provider.cfg.max_chars)
        except QuotaExhausted as exc:
            failures.append(f"{provider.name}: quota exhausted")
            print(f"    [{provider.name}] quota exhausted — falling through")
            continue
        except PaymentRequired as exc:
            failures.append(f"{provider.name}: payment required (no free quota)")
            print(f"    [{provider.name}] 402 payment required — falling through")
            continue
        except (RateLimited, RequestTooLarge, NotConfigured, ProviderError) as exc:
            failures.append(f"{provider.name}: {str(exc)[:90]}")
            print(f"    [{provider.name}] failed ({str(exc)[:70]}) — falling through")
            continue

    raise AllProvidersExhausted("; ".join(failures))


def load_settings() -> dict:
    with (ROOT / "config" / "settings.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def default_providers() -> list[ChatProvider]:
    return build_providers(load_settings())
