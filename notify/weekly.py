"""LAYER 3. The weekly report (docs/PLAN.md 9.1).

    python -m notify.weekly              # send (email)
    python -m notify.weekly --dry-run    # print, send nothing

Sunday morning, one message that answers "is this working?" from what the
pipeline recorded rather than from impressions. It leads with the plan's real
metric -- interviews per 10 applications -- then the inputs that drive it:
cadence, discovery and apply latency, pings, the funnel, referrals, board and
LLM health.

collect() reads; compose() is pure, so the report's arithmetic and wording are
tested without a database (tests/test_weekly.py).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DAYS = 7


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _first_seen_by_board(rows: list[dict]) -> dict[int, str]:
    first: dict[int, str] = {}
    for r in rows:
        cid, seen = r.get("company_id"), r.get("first_seen_at")
        if cid is not None and seen and (cid not in first or seen < first[cid]):
            first[cid] = seen
    return first


def collect(now: datetime | None = None) -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from common.settings import load_settings
    from notify.run import db_headers, get_all

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = db_headers()

    def safe(path: str) -> list[dict]:
        try:
            return get_all(path, h)
        except Exception:
            return []                       # a table predating its migration

    return {
        "now": now,
        "threshold": float(load_settings().get("ping_threshold", 6.5)),
        "runs": safe(f"run_log?select=started_at,trigger_source,errors&worker=eq.ingest"
                     f"&started_at=gte.{since}"),
        "triggers": safe(f"trigger_log?select=fired_at,source,outcome&fired_at=gte.{since}"),
        "new_jobs": safe(f"jobs?select=company_id,first_seen_at,posted_at,posted_at_precision"
                         f"&first_seen_at=gte.{since}"),
        # Each board's first sighting ever, to leave out boards added this week.
        "board_first_seen": _first_seen_by_board(safe("jobs?select=company_id,first_seen_at")),
        "scores": safe(f"job_scores?select=fit_score,provider,provisional&scored_at=gte.{since}"),
        "pings": safe(f"notifications?select=job_id,channel&ok=is.true&job_id=not.is.null"
                      f"&sent_at=gte.{since}"),
        "funnel": safe("v_funnel?select=applied_at,ping_to_apply_hours,interview_at,status,"
                       "rejected_at,responded_at,via_referral,channel"),
        "events": safe(f"application_events?select=kind,match_state&received_at=gte.{since}"),
        "asks": safe(f"referral_asks?select=outcome,asked_at&asked_at=gte.{since}"),
        "inactive": safe("companies?select=name&active=is.false"),
        "active": safe("companies?select=id&active=is.true&ats=neq.aggregator"),
        "usage": safe(f"provider_usage?select=provider,jobs_scored,quota_exhausted,errors"
                      f"&day=gte.{since[:10]}"),
    }


# Triggers that fire on a clock whatever else is happening (decided
# 2026-10-15). An hour without a fire from one of these means it has stopped.
# apps-script fires only when runs are stale, and GitHub's schedule is
# best-effort, so silence from them is normal and only reported.
CLOCKED_TRIGGERS = ("cronjob", "cf")
QUIET_AFTER = timedelta(hours=1)


def trigger_mix(rows: list[dict], now: datetime) -> list[str]:
    """One line per trigger: fires, how many ran vs collapsed into the lease,
    and when it last fired. A clocked trigger that went quiet is flagged."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("source") or "?", []).append(r)
    out = []
    for source in sorted(set(by) | set(CLOCKED_TRIGGERS),
                         key=lambda x: (x not in CLOCKED_TRIGGERS, x)):
        fires = by.get(source, [])
        ran = sum(1 for r in fires if r.get("outcome") == "ran")
        last = max((_ts(r["fired_at"]) for r in fires), default=None)
        when = "never" if last is None else f"last {(now - last).total_seconds() / 3600:.1f}h ago"
        flag = ""
        if source in CLOCKED_TRIGGERS and (last is None or now - last > QUIET_AFTER):
            flag = "  <- QUIET"
        out.append(f"    {source:<12} {len(fires):>4} fires: {ran} ran, "
                   f"{len(fires) - ran} collapsed; {when}{flag}")
    return out


def by_channel(applied: list[dict], interviewed: list[dict]) -> list[tuple[str, int, int]]:
    """[(channel, applications, interviews)] -- direct vs Indeed vs Naukri vs
    LinkedIn (applications.channel, migration 0023), busiest first."""
    counts = Counter(r.get("channel") or "unknown" for r in applied)
    wins = Counter(r.get("channel") or "unknown" for r in interviewed)
    return [(ch, n, wins.get(ch, 0)) for ch, n in counts.most_common()]


def compose(d: dict) -> tuple[str, str]:
    now: datetime = d["now"]
    week_ago = now - timedelta(days=DAYS)

    # Headline: interviews per 10 applications, all time and this week.
    applied = [r for r in d["funnel"] if r.get("applied_at")]
    interviewed = [r for r in applied if r.get("interview_at")
                   or r.get("status") in ("interviewing", "offer")]
    per10 = (len(interviewed) / len(applied) * 10) if applied else None
    applied_week = [r for r in applied if _ts(r["applied_at"]) >= week_ago]

    # Cadence: automated runs only.
    auto = sorted(_ts(r["started_at"]) for r in d["runs"]
                  if r.get("trigger_source") not in (None, "manual"))
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(auto, auto[1:])]
    sources = Counter(r.get("trigger_source") or "unrecorded" for r in d["runs"])
    errored_runs = sum(1 for r in d["runs"] if r.get("errors"))

    # Discovery latency: exact posting times only (Workday's are dates), and
    # only postings published this week. A board added this week brings its
    # whole back-catalogue in as "new" -- months-old postings first seen today
    # -- which is a backfill, not a discovery delay; counted, it reported a
    # 481-hour median on 2026-10-15.
    # Boards added this week are left out too: their postings from before the
    # board existed were not *late*, the board simply was not polled yet.
    new_boards = {cid for cid, first in d.get("board_first_seen", {}).items()
                  if _ts(first) >= week_ago}
    discover = [(_ts(j["first_seen_at"]) - _ts(j["posted_at"])).total_seconds() / 3600
                for j in d["new_jobs"]
                if j.get("posted_at") and j.get("posted_at_precision") == "timestamp"
                and _ts(j["posted_at"]) >= week_ago
                and j.get("company_id") not in new_boards]
    discover = [x for x in discover if x >= 0]

    scored = [s for s in d["scores"] if s.get("fit_score") is not None]
    relevant = [s for s in scored if float(s["fit_score"]) >= d["threshold"]]
    rules = [s for s in scored if s.get("provider") == "rules"]
    ping_to_apply = [float(r["ping_to_apply_hours"]) for r in applied_week
                     if r.get("ping_to_apply_hours") is not None]

    kinds = Counter(e.get("kind") for e in d["events"] if e.get("match_state") == "matched")
    review = sum(1 for e in d["events"] if e.get("match_state") in ("needs_review", "unmatched"))
    asks = Counter(a.get("outcome") or "awaiting reply" for a in d["asks"])
    llm = Counter()
    for u in d["usage"]:
        llm[u["provider"]] += int(u.get("jobs_scored") or 0)

    def h(x: float | None, unit: str = "h") -> str:
        return "—" if x is None else f"{x:.1f}{unit}"

    lines = [
        f"Week to {now:%a %d %b %Y}",
        "",
        f"INTERVIEWS PER 10 APPLICATIONS: {'—' if per10 is None else f'{per10:.1f}'}"
        f"  ({len(interviewed)} of {len(applied)} all time)",
        "",
        "Pipeline",
        f"  ingest runs: {len(d['runs'])} ({', '.join(f'{k} {v}' for k, v in sources.most_common())})",
        f"  median gap between automated runs: {h(_median(gaps), ' min')}  (target <= 17 min)",
        f"  runs with board errors: {errored_runs}",
        "  trigger mix (every fire, including those the lease collapsed):",
        *trigger_mix(d.get("triggers", []), now),
        f"  new postings: {len(d['new_jobs'])}; discovery latency median "
        f"{h(_median(discover))} (exact posting times, n={len(discover)})",
        f"  boards: {len(d['active'])} active, {len(d['inactive'])} retired",
        "",
        "Matches and pings",
        f"  scored: {len(scored)}; at or above {d['threshold']:g}: {len(relevant)}",
        f"  jobs pinged: {len({p.get('job_id') for p in d['pings']})}; deliveries: {len(d['pings'])} "
        f"({', '.join(f'{k} {v}' for k, v in Counter(p['channel'] for p in d['pings']).most_common()) or 'none'})",
        "",
        "You",
        f"  applications this week: {len(applied_week)}",
        f"  ping -> apply median: {h(_median(ping_to_apply))}  (target <= 3h)",
        f"  outcome emails matched: {sum(kinds.values())} "
        f"({', '.join(f'{k} {v}' for k, v in kinds.most_common()) or 'none'}); "
        f"waiting in Review: {review}",
        "  by channel (all time): " + (", ".join(
            f"{ch} {n} applied / {i} interview{'s' if i != 1 else ''}"
            for ch, n, i in by_channel(applied, interviewed)) or "none"),
        f"  referral asks: {len(d['asks'])} "
        f"({', '.join(f'{k} {v}' for k, v in asks.most_common()) or 'none'})",
        "",
        "Scoring health",
        f"  LLM jobs scored by provider: "
        f"{', '.join(f'{k} {v}' for k, v in llm.most_common()) or 'none recorded'}",
        f"  provisional (rules) scores: {len(rules)} of {len(scored)}"
        + (f"  <- over 30%" if scored and len(rules) / len(scored) > 0.30 else ""),
    ]
    if per10 is None or not applied_week:
        lines += ["", "Nothing to measure without applications: the funnel only fills "
                      "in from the Applied button and outcome emails."]
    subject = (f"[job agent] week: {len(relevant)} matches, {len(applied_week)} applied, "
               f"{'—' if per10 is None else f'{per10:.1f}'} interviews/10")
    return subject, "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    subject, body = compose(collect())
    print(f"Subject: {subject}\n\n{body}")
    if args.dry_run:
        return 0
    from notify import email as email_channel
    email_channel.send(subject, body)
    print("emailed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
