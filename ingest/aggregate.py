#!/usr/bin/env python
"""LAYER 1. Postings that do not come from an ATS board (PLAN 6.7, 2026-10-15).

    python -m ingest.aggregate --indeed            # hourly, .github/workflows/aggregate.yml
    python -m ingest.aggregate --inbox             # every ingest run: forwarded alert mail
    python -m ingest.aggregate --ttl               # close what has not been seen for a while
    python -m ingest.aggregate --indeed --dry-run  # fetch and plan, write nothing

Two sources:

  * Indeed, through python-jobspy: no login, country India, a handful of
    search terms, results_wanted kept small. Indeed is reached only from here.
  * Job-alert emails from LinkedIn, Naukri and Indeed, which the operator
    receives and apps_script/outcomes.gs forwards into alert_inbox. Neither
    LinkedIn nor Naukri is ever contacted; the email is the whole input.

What makes these different from a board, and so the rules here:

  * A search result is not a board. Absence from the next search says nothing,
    so nothing is ever closed on absence. A posting closes only after
    aggregator_ttl_days without a sighting (--ttl).
  * One posting reaches us several ways -- Indeed today, a LinkedIn alert
    tomorrow, and maybe the company's own ATS. The identity is therefore
    (company_key, normalised title), not any site's id: ats_job_id is
    "agg:" + a hash of that pair. And a posting already open on a polled ATS
    board at the same company is skipped outright.
  * Companies are rows with ats = 'aggregator' (board_token = company_key),
    separate from any roster row for the same employer. Attaching these jobs
    to a roster board would let that board's next diff close them.
  * A richer description wins: an alert gives a title only, Indeed gives the
    whole JD, and whichever arrives second must not blank the first.

New rows are scored and pinged by the normal ingest run, through the same
prefilter and the same threshold as every other job.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from common.company_key import company_key           # noqa: E402
from common.geo import is_india_relevant              # noqa: E402
from ingest.lifecycle import _payload                 # noqa: E402
from ingest.models import RawJob                      # noqa: E402
from ingest.normalize import clean_text, content_hash  # noqa: E402

SOURCES = ("indeed", "alert-email")


@dataclass
class Item:
    source: str                       # indeed | alert-email
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted_at: datetime | None = None
    raw: dict = field(default_factory=dict)


def norm_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9+#]+", " ", (title or "").lower())
    return " ".join(t.split())


def identity(item: Item) -> tuple[str, str]:
    return company_key(item.company), norm_title(item.title)


def agg_id(key: tuple[str, str]) -> str:
    return "agg:" + hashlib.sha1(f"{key[0]}|{key[1]}".encode("utf-8")).hexdigest()[:20]


@dataclass
class Plan:
    upserts: list[dict] = field(default_factory=list)
    touch_ids: list[int] = field(default_factory=list)
    skipped_ats: int = 0
    skipped_other: int = 0
    n_new: int = 0


def plan(items: list[Item], company_ids: dict[str, int], existing: dict[str, dict],
         ats_open: set[tuple[str, str]]) -> Plan:
    """Pure. `company_ids` maps company_key -> aggregator company id;
    `existing` maps ats_job_id -> {id, closed_at, reopen_count, description,
    source}; `ats_open` is {(company_key, norm_title)} open on polled boards."""
    out = Plan()
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = identity(item)
        if not key[0] or not key[1] or key in seen:
            out.skipped_other += 1
            continue
        seen.add(key)
        if key in ats_open:
            out.skipped_ats += 1
            continue
        cid = company_ids.get(key[0])
        if cid is None:
            out.skipped_other += 1
            continue
        jid = agg_id(key)
        prior = existing.get(jid)
        desc = clean_text(item.description)
        if prior and len(prior.get("description") or "") >= len(desc) \
                and prior.get("closed_at") is None:
            out.touch_ids.append(prior["id"])
            continue
        if prior and len(prior.get("description") or "") > len(desc):
            desc = prior["description"]
        job = RawJob(ats_job_id=jid, title=item.title.strip(), location=item.location.strip(),
                     description=desc, absolute_url=item.url, posted_at=item.posted_at,
                     posted_at_precision="date" if item.posted_at else None,
                     raw={"source": item.source, **item.raw})
        bump = (prior.get("reopen_count") or 0) + (1 if prior.get("closed_at") else 0) \
            if prior else 0
        row = _payload(cid, job, content_hash(job.title, job.location, desc), bump)
        # The first source to find a posting keeps the credit.
        row["source"] = (prior or {}).get("source") or item.source
        out.upserts.append(row)
        out.n_new += 0 if prior else 1
    return out


# --- sources ----------------------------------------------------------------------

def fetch_indeed(cfg: dict) -> list[Item]:
    """python-jobspy against Indeed India. Imported here so the ingest run,
    which never needs pandas, does not pay for it."""
    from jobspy import scrape_jobs

    items: list[Item] = []
    for term in cfg.get("search_terms") or []:
        try:
            df = scrape_jobs(site_name=["indeed"], search_term=term,
                             location=cfg.get("location", "India"),
                             country_indeed="India",
                             results_wanted=int(cfg.get("results_wanted", 15)),
                             hours_old=int(cfg.get("hours_old", 3)),
                             description_format="markdown", verbose=0)
        except Exception as exc:
            # One blocked term must not cost the others; the run reports it.
            print(f"  indeed {term!r}: {type(exc).__name__}: {str(exc)[:160]}")
            raise
        for _, r in df.iterrows():
            posted = r.get("date_posted")
            try:
                posted_at = (datetime(posted.year, posted.month, posted.day, tzinfo=timezone.utc)
                             if posted is not None and str(posted) != "NaT" else None)
            except AttributeError:
                posted_at = None
            items.append(Item(
                source="indeed", title=str(r.get("title") or ""),
                company=str(r.get("company") or ""), location=str(r.get("location") or ""),
                url=str(r.get("job_url") or ""),
                description=str(r.get("description") or "") if str(r.get("description")) != "nan" else "",
                posted_at=posted_at, raw={"site_job_id": str(r.get("id") or ""), "term": term}))
        print(f"  indeed {term!r}: {len(df)} result(s)")
        time.sleep(float(cfg.get("pause_seconds", 4)))
    return items


def items_from_alert(row: dict) -> list[Item]:
    from ingest.alert_parse import parse
    return [Item(source="alert-email", title=j.title, company=j.company, location=j.location,
                 url=j.url, raw={"site": j.site, "site_job_id": j.site_job_id,
                                 "message_id": row.get("message_id")})
            for j in parse(row.get("sender"), row.get("subject"), row.get("body"))]


# --- database glue ---------------------------------------------------------------

def ensure_companies(store, items: list[Item]) -> dict[str, int]:
    """company_key -> aggregator company id, creating rows as needed."""
    keys: dict[str, str] = {}
    for it in items:
        k = company_key(it.company)
        if k and k not in keys:
            keys[k] = it.company.strip()
    if not keys:
        return {}
    have = {r["board_token"]: r["id"] for r in store._select_all(
        "companies", "select=id,board_token&ats=eq.aggregator")}
    missing = [{"name": name, "ats": "aggregator", "board_token": k, "company_key": k,
                "priority": 3, "active": True}
               for k, name in keys.items() if k not in have]
    for i in range(0, len(missing), 200):
        rows = store._request("POST", "companies", params="on_conflict=ats,board_token",
                              body=missing[i:i + 200],
                              prefer="resolution=merge-duplicates,return=representation").json()
        have.update({r["board_token"]: r["id"] for r in rows})
    return {k: have[k] for k in keys if k in have}


def existing_for(store, company_ids: list[int]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(company_ids), 150):
        chunk = ",".join(str(c) for c in company_ids[i:i + 150])
        for r in store._select_all(
                "jobs", "select=id,ats_job_id,closed_at,reopen_count,description,source"
                f"&company_id=in.({chunk})"):
            out[r["ats_job_id"]] = r
    return out


def ats_open_keys(store, keys: set[str]) -> set[tuple[str, str]]:
    """(company_key, norm_title) for open jobs on polled boards at these companies."""
    if not keys:
        return set()
    out: set[tuple[str, str]] = set()
    ks = sorted(keys)
    for i in range(0, len(ks), 100):
        # Quoted for PostgREST, then URL-encoded: keys can carry "&", "#" or
        # "+" (company names do), which would otherwise split the query.
        chunk = quote(",".join('"' + k.replace('"', '') + '"' for k in ks[i:i + 100]),
                      safe='",')
        for r in store._select_all(
                "jobs", "select=id,title,companies!inner(company_key,ats)&closed_at=is.null"
                f"&companies.company_key=in.({chunk})&companies.ats=neq.aggregator"):
            out.add((r["companies"]["company_key"], norm_title(r["title"])))
    return out


def apply(store, items: list[Item], dry_run: bool) -> Plan:
    items = [it for it in items if it.title.strip() and it.company.strip() and it.url
             and is_india_relevant(it.location, it.description)]
    if dry_run:
        ids = {company_key(it.company): -1 for it in items}
        p = plan(items, ids, {}, set())
        print(f"  dry run: {len(p.upserts)} posting(s) would be written")
        return p
    ids = ensure_companies(store, items)
    p = plan(items, ids, existing_for(store, sorted(set(ids.values()))),
             ats_open_keys(store, set(ids)))
    store.upsert_jobs(p.upserts)
    store.touch_jobs(p.touch_ids)
    return p


def close_expired(store, ttl_days: float, dry_run: bool) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    rows = store._select_all("jobs", f"select=id&source=neq.ats&closed_at=is.null"
                                     f"&last_seen_at=lt.{cutoff}")
    if not dry_run:
        store.close_jobs(r["id"] for r in rows)
    return len(rows)


def drain_inbox(store, dry_run: bool, limit: int = 100) -> tuple[int, Plan | None]:
    rows = store._select_all("alert_inbox", f"select=id,message_id,sender,subject,body"
                                            f"&processed_at=is.null&order=id.asc&limit={limit}")
    if not rows:
        return 0, None
    items: list[Item] = []
    per_mail: dict[int, int] = {}
    errors: dict[int, str] = {}
    for r in rows:
        try:
            found = items_from_alert(r)
        except Exception as exc:                 # a parser bug must not stall the inbox
            found, errors[r["id"]] = [], f"{type(exc).__name__}: {exc}"[:300]
        per_mail[r["id"]] = len(found)
        items.extend(found)
    p = apply(store, items, dry_run)
    if not dry_run:
        stamp = datetime.now(timezone.utc).isoformat()
        for rid, n in per_mail.items():
            store._request("PATCH", "alert_inbox", params=f"id=eq.{rid}",
                           body={"processed_at": stamp, "jobs_found": n,
                                 "error": errors.get(rid)}, prefer="return=minimal")
    return len(rows), p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indeed", action="store_true")
    ap.add_argument("--inbox", action="store_true")
    ap.add_argument("--ttl", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.indeed or args.inbox or args.ttl):
        ap.error("pick at least one of --indeed, --inbox, --ttl")

    load_dotenv(ROOT / ".env")
    from common.settings import load_settings
    from ingest.store import Store
    cfg = load_settings(ROOT / "config" / "settings.yaml").get("aggregator") or {}
    store = Store()
    status = 0

    if args.inbox:
        n, p = drain_inbox(store, args.dry_run)
        if p is None:
            print("alert inbox: nothing waiting")
        else:
            print(f"alert inbox: {n} mail(s) -> {len(p.upserts)} written ({p.n_new} new), "
                  f"{len(p.touch_ids)} re-seen, {p.skipped_ats} already on an ATS board")

    if args.indeed:
        icfg = cfg.get("indeed") or {}
        if not icfg.get("enabled", True):
            print("indeed: disabled in settings")
        else:
            run_id = None if args.dry_run else store.start_run("aggregate")
            started = time.monotonic()
            try:
                items = fetch_indeed(icfg)
                p = apply(store, items, args.dry_run)
                print(f"indeed: {len(items)} result(s) -> {len(p.upserts)} written "
                      f"({p.n_new} new), {len(p.touch_ids)} re-seen, "
                      f"{p.skipped_ats} already on an ATS board")
                errors = []
            except Exception as exc:
                print(f"indeed: FAILED {type(exc).__name__}: {str(exc)[:300]}")
                items, p, errors, status = [], None, [{"company": "indeed",
                                                       "error": str(exc)[:300]}], 1
            if run_id is not None:
                store.finish_run(run_id, jobs_seen=len(items),
                                 jobs_new=p.n_new if p else 0, jobs_closed=0, errors=errors,
                                 boards=[{"name": "indeed", "seen": len(items)}],
                                 trigger_source="schedule",
                                 duration_s=round(time.monotonic() - started, 3))

    if args.ttl:
        n = close_expired(store, float(cfg.get("ttl_days", 21)), args.dry_run)
        print(f"ttl: {n} aggregator posting(s) unseen for {cfg.get('ttl_days', 21)}d "
              f"{'would be ' if args.dry_run else ''}closed")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
