#!/usr/bin/env python
"""LAYER 2 entrypoint — prefilter, then score what survives.

    python -m score.run                 # score everything unscored
    python -m score.run --limit 24      # small batch while tuning the prompt
    python -m score.run --prefilter-only
    python -m score.run --report        # re-print the table from the DB
    python -m score.run --rescore       # ignore existing scores

Prefilter rejections are written to job_scores too, with a reject_reason and no
fit_score. That keeps one row per job — so the same posting is never paid for
twice, and the funnel view can account for every job the system ever saw.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# LLM output routinely contains en-dashes and non-breaking hyphens. The Windows
# console defaults to cp1252, which cannot encode them, and an unhandled
# UnicodeEncodeError at print time would fail a run whose work is already
# committed to the database.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from ingest.normalize import is_india_relevant   # noqa: E402
from common.geo import in_preferred_location     # noqa: E402
from score.llm import (USAGE, AllProvidersExhausted, ScoringError,  # noqa: E402
                       apply_headcount_penalty, build_system_prompt,
                       default_providers, score_batch)
from score.providers import QuotaExhausted       # noqa: E402
from score.rules import Calibration, score_job   # noqa: E402


def simulate_outage(providers: list, names: str) -> None:
    """Chaos test (PLAN 7.5): make providers behave as if their quota ran out.

    Patches each chosen provider's complete() in this process only, so the
    healthcheck, the fall-through, the circuit breaker and the rules fallback
    are all exercised exactly as in a real outage.
    """
    chosen = {n.strip() for n in names.split(",") if n.strip()}

    def dead(*_a, **_k):
        raise QuotaExhausted(3600, "simulated outage (--simulate-outage)")

    for p in providers:
        if "all" in chosen or p.name in chosen:
            p.complete = dead            # type: ignore[method-assign]
            print(f"  [{p.name}] simulated outage")


def rules_rows(jobs: list[dict], min_years_by: dict[int, float | None],
               calibration: Calibration) -> list[dict]:
    return [score_job(j, calibration, min_years_by.get(j["id"])) for j in jobs]
from score import healthcheck                    # noqa: E402
from score.prefilter import prefilter            # noqa: E402
from score.store import ScoreStore               # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("score.run")

SETTINGS = ROOT / "config" / "settings.yaml"


def load_settings() -> dict:
    # One loader for every layer (common/settings.py, PLAN 8.3).
    from common.settings import load_settings as _load
    return _load(SETTINGS)


def truncate(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def job_age_days(job: dict) -> float | None:
    """Age in days from the ATS posting date, else our first sighting.

    Same precedence the dashboard and the notifier use, so a job's stated age is
    one number everywhere. Returns None when neither stamp parses, which leaves
    the age gate open rather than silently discarding the posting.
    """
    stamp = job.get("posted_at") or job.get("first_seen_at")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def print_table(rows: list[dict]) -> None:
    """score | title | company | stated exp | matched | gaps | reasoning"""
    if not rows:
        print("  (nothing scored)")
        return
    header = (f"{'score':>5}  {'title':<40}  {'company':<15}  {'exp':<8}  "
              f"{'matched':<34}  {'gaps':<28}  reasoning")
    print(header)
    print("-" * len(header))
    for row in rows:
        score = row.get("fit_score")
        exp = "unstated"
        lo, hi = row.get("min_years"), row.get("max_years")
        if lo is not None and hi is not None:
            exp = f"{float(lo):g}-{float(hi):g}y"
        elif lo is not None:
            exp = f"{float(lo):g}y+"
        print(f"{(f'{float(score):.1f}' if score is not None else '  -'):>5}  "
              f"{truncate(row.get('title'), 40):<40}  "
              f"{truncate(row.get('company'), 15):<15}  {exp:<8}  "
              f"{truncate(', '.join(row.get('matched_skills') or []), 34):<34}  "
              f"{truncate(', '.join(row.get('gap_skills') or []), 28):<28}  "
              f"{truncate(row.get('reasoning'), 90)}")


def distribution(rows: list[dict]) -> tuple[int, float]:
    """-> (count of 8+, percentage of scored jobs). The calibration number."""
    scored = [r for r in rows if r.get("fit_score") is not None]
    if not scored:
        return 0, 0.0
    high = sum(1 for r in scored if float(r["fit_score"]) >= 8.0)
    return high, high / len(scored) * 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="cap how many survivors get scored")
    ap.add_argument("--prefilter-only", action="store_true", help="no LLM calls")
    ap.add_argument("--report", action="store_true", help="print stored scores and exit")
    ap.add_argument("--rescore", action="store_true", help="ignore existing scores")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-score jobs whose previous scoring attempt errored")
    ap.add_argument("--provider", help="use only this provider (e.g. google)")
    ap.add_argument("--max-age-days", type=float,
                    help="skip postings older than this, before any LLM call "
                         "(default: max_age_days in settings.yaml). Pass 0 to "
                         "disable the age gate — needed on the first run after "
                         "adding a board, whose whole back catalogue is old")
    ap.add_argument("--batch-size", type=int,
                    help="override llm_batch_size — needed when falling back to "
                         "a model with a tighter per-minute cap")
    ap.add_argument("--char-limit", type=int,
                    help="override description_char_limit; the system prompt is "
                         "~2,600 tokens, so on an 8k/min model this is the only "
                         "lever left once batch size is already 1")
    ap.add_argument("--simulate-outage", metavar="NAMES",
                    help="chaos test (PLAN 7.5): comma-separated providers, or "
                         "'all', that act as if their quota were exhausted. The "
                         "run must still finish, falling back to the rules scorer")
    args = ap.parse_args()

    settings = load_settings()
    max_years = float(settings.get("max_experience_years", 4))
    penalties = settings.get("headcount_penalties") or {}
    # An explicit --max-age-days always wins, including over --rescore: asking
    # for both is a deliberate "re-score, but only the recent ones". Absent the
    # flag, --rescore disables the gate, since re-scoring an existing corpus is
    # deliberate and that corpus is old by definition. 0 from either source
    # disables it outright.
    if args.max_age_days is not None:
        max_age = args.max_age_days or None
    elif args.rescore:
        max_age = None
    else:
        max_age = float(settings.get("max_age_days", 0) or 0) or None
    if max_age:
        print(f"age gate: skipping postings older than {max_age:g} days")
    store = ScoreStore()
    if not args.report:
        # Before scoring, so a run that has nothing new still re-applies the
        # city rule to everything already scored (db/migrations/0019).
        try:
            flipped = store.sync_location_flags(in_preferred_location)
            if flipped:
                print(f"location: {flipped} scored job(s) changed location_ok")
        except Exception as exc:
            print(f"location: flags not synced ({type(exc).__name__}: {exc})")
    try:
        return _main(args, store, max_years, penalties, max_age)
    finally:
        # The ledger is written whatever happened, including a crash: a
        # failing provider is exactly what it exists to record.
        # Never from a chaos run: simulated quota failures would write real
        # cooldowns, and the next genuine run would skip every provider.
        if USAGE and not args.report and not args.simulate_outage:
            store.record_usage(USAGE)


def _main(args, store, max_years, penalties, max_age) -> int:
    providers = default_providers()
    if args.provider:
        providers = [p for p in providers if p.name == args.provider]
    if args.simulate_outage:
        simulate_outage(providers, args.simulate_outage)
    configured = [p for p in providers if p.available()]

    # Circuit breaker carried across runs (db/migrations/0014): a provider that
    # exhausted its quota earlier is not called again until its cooldown ends.
    tripped: set[str] = set()
    if not args.report and not args.prefilter_only:
        for name, until in store.cooldowns().items():
            tripped.add(name)
            print(f"  [{name}] circuit open until {until[:16]} (provider ledger)")

    # With no working LLM the run still finishes: survivors get a provisional
    # rules score (score/rules.py) instead of the old exit code 2, which in the
    # workflow also skipped Notify -- an outage silenced every ping.
    rules_only = False
    if not args.prefilter_only and not args.report:
        # Ping every provider before committing to a run. A dead key or an
        # unpaid account makes a three-deep chain one-deep, and finding that
        # out after the lead provider hits its cap wastes the whole window.
        try:
            results = healthcheck.run(providers, fail_fast=True)
            healthy = {h.name for h in results if h.live}
        except RuntimeError as exc:
            log.error("%s", exc)
            print(f"\n{exc}", file=sys.stderr)
            healthy = set()
        configured = [p for p in configured if p.name in healthy and p.name not in tripped]
        if not configured:
            rules_only = True
            print("\nNO LLM AVAILABLE — scoring with the rules fallback; every "
                  "score this run is provisional and will be redone by the next "
                  "run that has a working provider.")
    else:
        print("providers: " + ", ".join(
            f"{p.name}({'ready' if p.available() else 'off'})" for p in providers))

    # Batch size and char limit follow the first ready provider unless overridden;
    # a fallback with a tighter ceiling re-chunks inside score_batch.
    lead = configured[0] if configured else (providers[0] if providers else None)
    batch_size = args.batch_size or (lead.cfg.batch_size if lead else 10)
    char_limit = args.char_limit or (lead.cfg.max_chars if lead else 6000)

    # Jobs that already carry a real verdict. A scoring_failed row is a
    # transport error, not a judgement, and must never overwrite one of these:
    # a rate-limited run would otherwise erase good scores and silently empty
    # the queue. This also makes two concurrent runs safe.
    already_scored = {s["job_id"] for s in store.scores()
                      if s.get("fit_score") is not None}

    def persist(rows: list[dict]) -> list[dict]:
        keep = [r for r in rows
                if r.get("fit_score") is not None or r["job_id"] not in already_scored]
        dropped = len(rows) - len(keep)
        if dropped:
            print(f"    (kept {dropped} existing score(s) rather than "
                  f"overwriting with a failure row)")
        store.upsert_scores(keep)
        already_scored.update(r["job_id"] for r in keep
                              if r.get("fit_score") is not None)
        return keep

    if args.report:
        jobs = {j["id"]: j for j in store.open_jobs(only_unscored=False)}
        rows = []
        for score in store.scores():
            job = jobs.get(score["job_id"])
            if not job or score.get("fit_score") is None:
                continue
            rows.append({**score, "title": job["title"],
                         "company": (job.get("companies") or {}).get("name", "")})
        print_table(rows)
        high, pct = distribution(rows)
        print(f"\n{len(rows)} scored — {high} at 8+ ({pct:.0f}%)")
        return 0

    if args.retry_failed:
        # A scoring_failed row means the model never gave a verdict — a
        # transport problem, not a judgement. Those jobs are invisible to the
        # queue until retried, so they must not be left behind silently.
        failed = {s["job_id"] for s in store.scores()
                  if (s.get("reject_reason") or "").startswith("scoring_failed")}
        jobs = [j for j in store.open_jobs(only_unscored=False) if j["id"] in failed]
        print(f"{len(jobs)} job(s) with a failed scoring attempt\n")
    else:
        jobs = store.open_jobs(only_unscored=not args.rescore)
        print(f"{len(jobs)} open job(s) to consider\n")
    # No early return when nothing new arrived: the recovery step below
    # (provisional and scoring_failed jobs) must run on quiet runs too, or a
    # stranded job waits for unrelated postings to show up. Found by the
    # end-to-end outage test on 2026-10-15.
    if not jobs and args.retry_failed:
        return 0

    survivors: list[dict] = []
    min_years_by: dict[int, float | None] = {}
    rejects: list[dict] = []
    reasons: Counter[str] = Counter()

    # A prefilter reject overwrites whatever score a job already had. On a
    # normal run that is harmless — only unscored jobs are loaded — but under
    # --rescore it will demote real LLM judgements to a one-line reject and
    # destroy the reasoning behind them. Tightening a rule then re-scoring is
    # exactly when that happens, and it is silent and irreversible: it cost 144
    # scores on 2026-09-04 before this guard existed.
    already_llm_scored: set[int] = set()
    if args.rescore or args.retry_failed:
        already_llm_scored = {s["job_id"] for s in store.scores()
                              if s.get("fit_score") is not None}
    preserved = 0

    for job in jobs:
        result = prefilter(
            job["title"], job.get("description") or "",
            # India (the board's gate) and the operator's city (2026-10-16).
            location_ok=is_india_relevant(job.get("location"), job.get("description") or "")
            and in_preferred_location(job.get("location"), job.get("description") or ""),
            max_experience_years=max_years,
            age_days=job_age_days(job),
            max_age_days=max_age,
        )
        if result.passed:
            survivors.append(job)
            min_years_by[job["id"]] = result.min_years
        elif job["id"] in already_llm_scored:
            # Leave the stored score untouched. The job is excluded from this
            # run rather than downgraded, so the new rule governs what gets
            # scored next without erasing what was already learned.
            preserved += 1
        else:
            reasons[result.reject_reason.split(":")[0]] += 1
            rejects.append({
                "job_id": job["id"], "fit_score": None, "verdict": "reject",
                "reject_reason": result.reject_reason,
                "min_years": result.min_years, "max_years": result.max_years,
                "model": "prefilter",
            })

    print(f"prefilter: {len(survivors)} passed, {len(rejects)} rejected")
    for reason, count in reasons.most_common():
        print(f"    {count:>4}  {reason}")
    if preserved:
        print(f"    {preserved:>4}  kept — already carry an LLM score, "
              f"not downgraded to a prefilter reject")
    store.upsert_scores(rejects)

    if args.prefilter_only:
        return 0

    # Recovery without a manual flag (PLAN 7.4). Jobs scored provisionally by
    # the rules fallback, and jobs left scoring_failed by an older run, re-enter
    # scoring here. They passed the prefilter when first seen, so they skip it
    # now -- otherwise the age gate would turn a provisional score into an age
    # reject and drop a live job from the queue. With no LLM this run, only the
    # failed ones are taken (a provisional score redone by rules is the same).
    if not args.retry_failed and not args.rescore:
        provisional = store.provisional_job_ids()
        failed = {s["job_id"] for s in store.scores()
                  if (s.get("reject_reason") or "").startswith("scoring_failed")}
        again = failed if rules_only else (provisional | failed)
        have = {j["id"] for j in survivors}
        if again - have:
            retry = [j for j in store.open_jobs(only_unscored=False)
                     if j["id"] in again and j["id"] not in have]
            if retry:
                print(f"re-scoring {len(retry)} job(s) left provisional or failed "
                      f"by an earlier run")
                survivors.extend(retry)

    if args.limit:
        survivors = survivors[: args.limit]

    calibration: Calibration | None = None

    def provisional_scores(jobs_: list[dict]) -> list[dict]:
        nonlocal calibration
        if calibration is None:
            calibration = Calibration.from_history(store.llm_history())
        return rules_rows(jobs_, min_years_by, calibration)

    by_id_all = {j["id"]: j for j in survivors}
    scored_rows: list[dict] = []

    if rules_only and survivors:
        rows = persist(provisional_scores(survivors))
        scored_rows.extend(rows)
        print(f"  {len(rows)} job(s) scored provisionally by rules")
        if args.simulate_outage:
            print("  (simulated outage: no alert sent)")
        else:
            try:
                from notify.alerts import alert_providers_exhausted
                alert_providers_exhausted("no LLM provider available at the start of the run; "
                                          "jobs scored provisionally by rules", 0)
            except Exception as alert_exc:
                log.error("could not send exhaustion alert: %s", alert_exc)
        survivors_to_llm: list[dict] = []
    else:
        survivors_to_llm = survivors

    system = build_system_prompt() if survivors_to_llm else ""
    if survivors_to_llm:
        print(f"\nscoring {len(survivors_to_llm)} job(s), batches of {batch_size}, "
              f"lead provider {lead.name} ({lead.cfg.model})...")

    for i in range(0, len(survivors_to_llm), batch_size):
        batch = survivors_to_llm[i: i + batch_size]
        started = time.monotonic()
        try:
            rows = score_batch(batch, providers, system=system, tripped=tripped)
            for row in rows:
                band = (by_id_all.get(row["job_id"], {}).get("companies")
                        or {}).get("headcount_band")
                apply_headcount_penalty(row, band, penalties)
        except AllProvidersExhausted as exc:
            stranded = survivors_to_llm[i:]
            log.error("ALL PROVIDERS EXHAUSTED after %d of %d job(s): %s",
                      i, len(survivors_to_llm), exc)
            print("\nERROR: every configured provider refused.")
            print(f"  {exc}")
            # Not left out of the queue: the rest of the run is scored by the
            # rules fallback, marked provisional, and re-scored automatically
            # by the next run with a working provider. (This used to mark them
            # scoring_failed, which no ordinary run ever retried.)
            rows = persist(provisional_scores(stranded))
            scored_rows.extend(rows)
            print(f"  {len(stranded)} job(s) scored provisionally by rules; the "
                  f"next run with a working provider redoes them")
            if args.simulate_outage:
                print("  (simulated outage: no alert sent)")
            else:
                try:
                    from notify.alerts import alert_providers_exhausted
                    alert_providers_exhausted(str(exc)[:400], len(stranded))
                except Exception as alert_exc:
                    log.error("could not send exhaustion alert: %s", alert_exc)
            break
        except ScoringError as exc:
            print(f"  batch {i // batch_size + 1}: {exc}")
            continue
        rows = persist(rows)
        scored_rows.extend(rows)
        ok = sum(1 for r in rows if r.get("fit_score") is not None)
        print(f"  batch {i // batch_size + 1}: {ok}/{len(batch)} scored "
              f"in {time.monotonic() - started:.1f}s")
        if i + batch_size < len(survivors_to_llm):
            time.sleep(2)     # stay well inside the free-tier rate limit

    by_id = {j["id"]: j for j in survivors}
    table = [{**r,
              "title": by_id[r["job_id"]]["title"],
              "company": (by_id[r["job_id"]].get("companies") or {}).get("name", "")}
             for r in scored_rows if r["job_id"] in by_id]
    table.sort(key=lambda r: (r.get("fit_score") is None,
                              -(float(r.get("fit_score") or 0))))

    print()
    print_table(table)

    high, pct = distribution(table)
    counts = Counter(r["verdict"] for r in table)
    print(f"\nverdicts: " + "  ".join(f"{v}={counts.get(v, 0)}" for v in
                                      ("strong", "worth_trying", "stretch", "reject")))
    print(f"calibration: {high} of {len([r for r in table if r.get('fit_score') is not None])} "
          f"scored 8+ ({pct:.0f}%)")
    if pct > 20:
        print("  ^ above the 20% ceiling in scoring_rules — the prompt needs tightening.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
