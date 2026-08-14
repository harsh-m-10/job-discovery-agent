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
import sys
import time
from collections import Counter
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
from score.llm import (AllProvidersExhausted, ScoringError,  # noqa: E402
                       apply_headcount_penalty, build_system_prompt,
                       default_providers, score_batch)
from score.prefilter import prefilter            # noqa: E402
from score.store import ScoreStore               # noqa: E402

SETTINGS = ROOT / "config" / "settings.yaml"


def load_settings() -> dict:
    with SETTINGS.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def truncate(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


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
    ap.add_argument("--batch-size", type=int,
                    help="override llm_batch_size — needed when falling back to "
                         "a model with a tighter per-minute cap")
    ap.add_argument("--char-limit", type=int,
                    help="override description_char_limit; the system prompt is "
                         "~2,600 tokens, so on an 8k/min model this is the only "
                         "lever left once batch size is already 1")
    args = ap.parse_args()

    settings = load_settings()
    max_years = float(settings.get("max_experience_years", 4))
    penalties = settings.get("headcount_penalties") or {}
    store = ScoreStore()

    providers = default_providers()
    if args.provider:
        providers = [p for p in providers if p.name == args.provider]
    configured = [p for p in providers if p.available()]
    print("providers: " + ", ".join(
        f"{p.name}({'ready' if p.available() else 'no key'})" for p in providers))
    if not configured and not args.prefilter_only:
        print("\nNo provider has an API key set. Expected one of: "
              + ", ".join(p.cfg.api_key_env for p in providers), file=sys.stderr)
        return 2

    # Batch size and char limit follow the first ready provider unless overridden;
    # a fallback with a tighter ceiling re-chunks inside score_batch.
    lead = configured[0] if configured else providers[0]
    batch_size = args.batch_size or lead.cfg.batch_size
    char_limit = args.char_limit or lead.cfg.max_chars

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
    if not jobs:
        return 0

    survivors: list[dict] = []
    rejects: list[dict] = []
    reasons: Counter[str] = Counter()

    for job in jobs:
        result = prefilter(
            job["title"], job.get("description") or "",
            location_ok=is_india_relevant(job.get("location"), job.get("description") or ""),
            max_experience_years=max_years,
        )
        if result.passed:
            survivors.append(job)
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
    store.upsert_scores(rejects)

    if args.prefilter_only:
        return 0
    if args.limit:
        survivors = survivors[: args.limit]

    system = build_system_prompt()
    print(f"\nscoring {len(survivors)} job(s), batches of {batch_size}, "
          f"lead provider {lead.name} ({lead.cfg.model})...")
    by_id_all = {j["id"]: j for j in survivors}

    scored_rows: list[dict] = []
    for i in range(0, len(survivors), batch_size):
        batch = survivors[i: i + batch_size]
        started = time.monotonic()
        try:
            rows = score_batch(batch, providers, system=system)
            for row in rows:
                band = (by_id_all.get(row["job_id"], {}).get("companies")
                        or {}).get("headcount_band")
                apply_headcount_penalty(row, band, penalties)
        except AllProvidersExhausted as exc:
            remaining = len(survivors) - i
            print("\nStopping: every configured provider refused.")
            print(f"  {exc}")
            print(f"  {remaining} job(s) left unscored and unmarked — they stay "
                  f"in the normal queue and are picked up by the next run.")
            break
        except ScoringError as exc:
            print(f"  batch {i // batch_size + 1}: {exc}")
            continue
        rows = persist(rows)
        scored_rows.extend(rows)
        ok = sum(1 for r in rows if r.get("fit_score") is not None)
        print(f"  batch {i // batch_size + 1}: {ok}/{len(batch)} scored "
              f"in {time.monotonic() - started:.1f}s")
        if i + batch_size < len(survivors):
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
