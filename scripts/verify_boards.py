#!/usr/bin/env python
"""Phase 0 — board verifier.

Hits every seed token, reports live/dead/empty with job counts, and writes the
verified entries to seeds/companies.verified.yaml. With --write-db (and Supabase
env vars set) it also upserts them into the `companies` table.

Seed tokens are unverified guesses: companies change ATS, tokens get renamed,
and nothing downstream can be trusted until this passes. For every dead token it
also probes the other two ATS platforms with the same token, which catches the
common "they moved from Greenhouse to Lever" case.

Usage:
    python scripts/verify_boards.py
    python scripts/verify_boards.py --write-db
    python scripts/verify_boards.py --only razorpay,stripe
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
SEEDS = ROOT / "seeds" / "companies.yaml"
VERIFIED = ROOT / "seeds" / "companies.verified.yaml"
REPORT = ROOT / "seeds" / "verify_report.json"
SETTINGS = ROOT / "config" / "settings.yaml"

UA = {"User-Agent": "job-agent/0.1 (personal job search; contact via repo owner)"}
# Workday fronts its tenants with a bot filter that rejects non-browser agents.
BROWSER_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# Verify-time only: cheap endpoints (no full JD content) to keep this fast and
# polite. The Phase 1 adapters use the content-bearing variants.
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}

INDIA_RE = re.compile(r"bengaluru|bangalore|karnataka|hyderabad|india", re.I)
REMOTE_RE = re.compile(r"remote", re.I)


@dataclass
class Result:
    name: str
    ats: str
    token: str
    category: str | None
    priority: int
    status: str = "dead"          # live | empty | dead | error
    jobs_total: int = 0
    jobs_india: int = 0
    http: int | None = None
    detail: str = ""
    alternate: str | None = None  # ATS the token was actually found on
    india_samples: list[str] = field(default_factory=list)


def load_settings() -> dict:
    with SETTINGS.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def looks_india(location: str | None) -> bool:
    """India-located, or remote roles that name India. Deliberately stricter
    than the Layer 1 keyword filter — 'Remote - US' must not count here."""
    if not location:
        return False
    if INDIA_RE.search(location):
        return True
    return bool(REMOTE_RE.search(location) and INDIA_RE.search(location))


def extract(ats: str, payload) -> list[tuple[str, str]]:
    """-> [(title, location)] for whichever ATS shape we got."""
    out: list[tuple[str, str]] = []
    if ats == "greenhouse":
        for j in (payload or {}).get("jobs", []):
            loc = (j.get("location") or {}).get("name") or ""
            out.append((j.get("title") or "", loc))
    elif ats == "lever":
        for j in payload or []:
            cats = j.get("categories") or {}
            loc = cats.get("location") or ""
            extra = j.get("workplaceType") or ""
            out.append((j.get("text") or "", f"{loc} {extra}".strip()))
    elif ats == "ashby":
        for j in (payload or {}).get("jobs", []):
            secondary = " ".join(
                s.get("location", "") for s in (j.get("secondaryLocations") or [])
            )
            loc = f"{j.get('location') or ''} {secondary}".strip()
            out.append((j.get("title") or "", loc))
    return out


def probe_workday(token: str, timeout: int):
    """Workday speaks POST and encodes tenant/host/site in the token.

    Status codes read backwards here: 404 means the tenant is real but the site
    name is wrong, 422 means the tenant is not on that host at all.
    """
    try:
        tenant, host, site = token.split("|")
    except ValueError:
        return "dead", None, "token must be 'tenant|host|site'", []

    url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        resp = requests.post(
            url, timeout=timeout,
            headers={**BROWSER_UA, "Content-Type": "application/json"},
            data=json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0,
                             "searchText": ""}),
        )
    except requests.RequestException as exc:
        return "error", None, f"{type(exc).__name__}: {exc}", []

    if resp.status_code == 404:
        return "dead", 404, "site name wrong for this tenant", []
    if resp.status_code == 422:
        return "dead", 422, "tenant not on this wd host", []
    if resp.status_code != 200:
        return "error", resp.status_code, f"http {resp.status_code}", []
    try:
        payload = resp.json()
    except ValueError:
        return "error", resp.status_code, "got the SPA shell, not the API", []

    postings = payload.get("jobPostings")
    if postings is None:
        return "dead", resp.status_code, "no jobPostings in response", []
    jobs = [(p.get("title") or "", p.get("locationsText") or "") for p in postings]
    return ("live" if jobs else "empty"), resp.status_code, "", jobs


def probe(ats: str, token: str, timeout: int) -> tuple[str, int | None, str, list[tuple[str, str]]]:
    """-> (status, http_code, detail, jobs)."""
    if ats == "workday":
        return probe_workday(token, timeout)
    url = ENDPOINTS[ats].format(token=token)
    last_err = ""
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=UA, timeout=timeout)
        except requests.RequestException as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            continue

        if resp.status_code == 404:
            return "dead", 404, "not found", []
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            last_err = f"http {resp.status_code}"
            continue
        if resp.status_code != 200:
            return "error", resp.status_code, f"http {resp.status_code}", []

        try:
            payload = resp.json()
        except ValueError:
            return "error", resp.status_code, "non-json response", []

        # Some boards answer 200 with an error envelope instead of 404.
        if isinstance(payload, dict) and (payload.get("error") or payload.get("errors")):
            return "dead", resp.status_code, str(payload.get("error") or payload.get("errors"))[:120], []

        jobs = extract(ats, payload)
        return ("live" if jobs else "empty"), resp.status_code, "", jobs

    return "error", None, last_err or "unreachable", []


def verify(entry: dict, timeout: int) -> Result:
    res = Result(
        name=entry["name"],
        ats=entry["ats"],
        token=str(entry["token"]),
        category=entry.get("category"),
        priority=int(entry.get("priority", 2)),
    )
    status, code, detail, jobs = probe(res.ats, res.token, timeout)
    res.status, res.http, res.detail = status, code, detail

    if status in ("dead", "error"):
        # Did they just switch ATS? Same token, other platforms. Workday tokens
        # are compound and never valid elsewhere, so skip the cross-probe.
        for other in (() if res.ats == "workday" else ENDPOINTS):
            if other == res.ats:
                continue
            alt_status, _, _, alt_jobs = probe(other, res.token, timeout)
            if alt_status == "live":
                res.alternate = other
                res.detail = (res.detail + f"; token is live on {other}").strip("; ")
                break
        return res

    res.jobs_total = len(jobs)
    india = [f"{t} — {l}" for t, l in jobs if looks_india(l)]
    res.jobs_india = len(india)
    res.india_samples = india[:3]
    return res


def upsert_companies(rows: list[dict]) -> None:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("\n--write-db given but SUPABASE_URL / SUPABASE_SERVICE_KEY are unset — skipped.")
        return
    resp = requests.post(
        f"{url}/rest/v1/companies?on_conflict=ats,board_token",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
            **UA,
        },
        data=json.dumps(rows),
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"\nSupabase upsert failed: {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    print(f"\nUpserted {len(rows)} companies into Supabase.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true", help="upsert verified boards into Supabase")
    ap.add_argument("--only", help="comma-separated tokens to check")
    ap.add_argument("--seeds", nargs="+", default=[str(SEEDS)],
                    help="seed files to verify (default: seeds/companies.yaml). "
                         "Pass several to verify batches together — the verified "
                         "output is the merged, deduplicated result.")
    ap.add_argument("--drop-empty", action="store_true",
                    help="exclude boards that answered 200 with zero postings "
                         "(they are real boards with nothing open right now, so kept by default)")
    args = ap.parse_args()

    settings = load_settings()
    timeout = int(settings.get("fetch_timeout_seconds", 20))
    workers = int(settings.get("fetch_concurrency", 8))

    seeds: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    for seed_path in args.seeds:
        with Path(seed_path).open(encoding="utf-8") as fh:
            for entry in yaml.safe_load(fh) or []:
                key = (entry["ats"], str(entry["token"]).lower())
                if key in seen_keys:
                    print(f"  (skipping duplicate {entry['name']} {key[0]}/{key[1]})")
                    continue
                seen_keys.add(key)
                seeds.append(entry)

    if args.only:
        wanted = {t.strip().lower() for t in args.only.split(",")}
        seeds = [s for s in seeds if str(s["token"]).lower() in wanted]

    print(f"Verifying {len(seeds)} boards with {workers} workers...\n")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda s: verify(s, timeout), seeds))

    results.sort(key=lambda r: (r.status != "live", -r.jobs_india, r.name.lower()))

    width = max(len(r.name) for r in results) + 1
    for r in results:
        mark = {"live": "OK  ", "empty": "EMPTY", "dead": "DEAD", "error": "ERR "}[r.status]
        line = f"{mark} {r.name:<{width}} {r.ats:<10} {r.token:<20}"
        if r.status == "live":
            line += f" {r.jobs_total:>4} jobs, {r.jobs_india:>3} India"
        elif r.detail:
            line += f" {r.detail}"
        print(line)

    live = [r for r in results if r.status == "live"]
    empty = [r for r in results if r.status == "empty"]
    dead = [r for r in results if r.status in ("dead", "error")]
    moved = [r for r in dead if r.alternate]

    print(f"\n{'-' * 60}")
    print(f"live: {len(live)}   empty: {len(empty)}   dead/error: {len(dead)}")
    print(f"total postings seen: {sum(r.jobs_total for r in live)}"
          f"   India-matching: {sum(r.jobs_india for r in live)}")
    if moved:
        print("\nTokens that resolve on a different ATS — fix seeds/companies.yaml:")
        for r in moved:
            print(f"  {r.name}: {r.ats} -> {r.alternate}")
    if dead:
        print("\nNeeds a manual token lookup (careers page -> Apply -> read the URL):")
        for r in dead:
            if not r.alternate:
                print(f"  {r.name} ({r.ats}/{r.token}) — {r.detail or 'unknown'}")

    keep = live if args.drop_empty else live + empty
    verified_rows = [
        {
            "name": r.name,
            "ats": r.ats,
            "token": r.token,
            "category": r.category,
            "priority": r.priority,
        }
        for r in sorted(keep, key=lambda r: (r.priority, r.name.lower()))
    ]
    VERIFIED.write_text(
        "# Generated by scripts/verify_boards.py — do not hand-edit.\n"
        "# Edit seeds/companies.yaml and re-run the verifier.\n"
        + yaml.safe_dump(verified_rows, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    REPORT.write_text(
        json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8"
    )
    print(f"\nWrote {len(verified_rows)} verified boards -> {VERIFIED.relative_to(ROOT)}")
    print(f"Full report -> {REPORT.relative_to(ROOT)}")

    if args.write_db:
        upsert_companies([
            {
                "name": r["name"],
                "ats": r["ats"],
                "board_token": r["token"],
                "category": r["category"],
                "priority": r["priority"],
                "active": True,
                "consecutive_failures": 0,
            }
            for r in verified_rows
        ])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
