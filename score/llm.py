"""LAYER 2, stage 2 — batch scoring across multiple LLM providers.

The prompt is assembled from the candidate profile (see `profile_path`) so that
resume facts and calibration rules live in one operator-editable file that is
kept out of version control, never in code. The
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
import os
import re
import time
from pathlib import Path

import yaml

from datetime import datetime, timedelta, timezone

from .providers import (ChatProvider, NotConfigured, PaymentRequired,
                        ProviderError, QuotaExhausted, RateLimited,
                        RequestTooLarge, ServerError, build_providers)

# --- usage ledger and circuit breaker (docs/PLAN.md 7.1, 7.2) ---------------
#
# USAGE accumulates per-provider counts for this process; score/run.py writes
# them to provider_usage at the end of the run. A provider whose quota is
# exhausted, or that has no free quota at all, gets a cooldown: score_batch
# stops calling it for the rest of the run, and the next runs read the
# cooldown back from the ledger. Before this, a dead provider was retried with
# backoff on every batch of every run.

QUOTA_COOLDOWN = timedelta(hours=1)        # floor; the vendor's hint wins if longer
PAYMENT_COOLDOWN = timedelta(hours=24)

USAGE: dict[str, dict] = {}


def usage(name: str) -> dict:
    return USAGE.setdefault(name, {
        "requests": 0, "successes": 0, "rate_limited": 0, "quota_exhausted": 0,
        "errors": 0, "jobs_scored": 0, "last_success_at": None, "last_error": None,
        "cooldown_until": None})


def _call(provider: ChatProvider, system: str, user: str, max_output: int) -> str:
    """provider.complete, counted. Re-raises everything."""
    u = usage(provider.name)
    u["requests"] += 1
    now = datetime.now(timezone.utc)
    try:
        raw = provider.complete(system, user, max_output)
    except QuotaExhausted as exc:
        u["quota_exhausted"] += 1
        u["last_error"] = f"quota exhausted: {str(exc)[:200]}"
        u["cooldown_until"] = now + max(QUOTA_COOLDOWN, timedelta(seconds=exc.retry_after or 0))
        raise
    except PaymentRequired as exc:
        u["errors"] += 1
        u["last_error"] = f"payment required: {str(exc)[:200]}"
        u["cooldown_until"] = now + PAYMENT_COOLDOWN
        raise
    except RateLimited as exc:
        u["rate_limited"] += 1
        u["last_error"] = f"rate limited: {str(exc)[:200]}"
        raise
    except Exception as exc:
        u["errors"] += 1
        u["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        raise
    u["successes"] += 1
    u["last_success_at"] = now
    return raw

ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "score" / "prompts" / "scoring.txt"

#: The profile carries compensation, employer and education facts, so it is
#: deliberately absent from version control — this repository is public. CI
#: decodes CANDIDATE_PROFILE_B64 to a temp file and sets CANDIDATE_PROFILE_PATH;
#: locally the file sits beside the LinkedIn export under ~/.job-agent/private.
#: The in-repo path stays last so a local copy there still resolves.
PRIVATE_PROFILE = Path.home() / ".job-agent" / "private" / "candidate_profile.yaml"
PROFILE_FILE = ROOT / "config" / "candidate_profile.yaml"

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I | re.M)

#: Output allowance per job. Entries run ~150 tokens; this leaves headroom
#: without wasting budget, since requested output counts against most caps.
OUTPUT_TOKENS_PER_JOB = 320


class ScoringError(RuntimeError):
    pass


class AllProvidersExhausted(ScoringError):
    """Every configured provider refused. Callers must stop, not retry."""


class ProfileMissing(ScoringError):
    """No candidate profile on disk. Scoring cannot run without one."""


def profile_path() -> Path:
    """First readable profile, in precedence order.

    Fails loudly rather than scoring against an empty profile: a silent
    fallback would send every job to the model with no resume facts and quietly
    produce meaningless scores.
    """
    override = os.environ.get("CANDIDATE_PROFILE_PATH")
    candidates = ([Path(override)] if override else []) + [PRIVATE_PROFILE,
                                                           PROFILE_FILE]
    for path in candidates:
        if path.is_file():
            return path
    raise ProfileMissing(
        "no candidate profile found. Looked in: "
        + ", ".join(str(p) for p in candidates)
        + f". Copy config/candidate_profile.example.yaml to {PRIVATE_PROFILE} "
        "and fill it in, or set CANDIDATE_PROFILE_PATH.")


def load_profile() -> dict:
    with profile_path().open(encoding="utf-8") as fh:
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
            raw = _call(provider, system, user, max_output)
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
        usage(provider.name)["jobs_scored"] += sum(
            1 for r in rows if r.get("fit_score") is not None)
        return rows

    raise ProviderError(f"{provider.name}: {last_error}")


def score_batch(jobs: list[dict], providers: list[ChatProvider], *,
                system: str | None = None,
                tripped: set[str] | None = None) -> list[dict]:
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
    # `tripped` is the run's circuit breaker: providers that already proved
    # unusable are not called again for later batches.
    tripped = tripped if tripped is not None else set()

    failures: list[str] = []
    for provider in usable:
        if provider.name in tripped:
            failures.append(f"{provider.name}: circuit open")
            continue
        try:
            return _attempt(provider, jobs, system, provider.cfg.max_chars)
        except QuotaExhausted as exc:
            tripped.add(provider.name)
            failures.append(f"{provider.name}: quota exhausted")
            print(f"    [{provider.name}] quota exhausted — circuit opened, falling through")
            continue
        except PaymentRequired as exc:
            tripped.add(provider.name)
            failures.append(f"{provider.name}: payment required (no free quota)")
            print(f"    [{provider.name}] 402 payment required — circuit opened, falling through")
            continue
        except RateLimited as exc:
            # Still limited after _attempt's own backoff: waiting again on the
            # next batch only burns the run. Skip it for the rest of this run.
            tripped.add(provider.name)
            failures.append(f"{provider.name}: rate limited after retries")
            print(f"    [{provider.name}] still rate limited — circuit opened, falling through")
            continue
        except (RequestTooLarge, NotConfigured, ProviderError) as exc:
            failures.append(f"{provider.name}: {str(exc)[:90]}")
            print(f"    [{provider.name}] failed ({str(exc)[:70]}) — falling through")
            continue

    raise AllProvidersExhausted("; ".join(failures))


def load_settings() -> dict:
    with (ROOT / "config" / "settings.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def default_providers() -> list[ChatProvider]:
    return build_providers(load_settings())
