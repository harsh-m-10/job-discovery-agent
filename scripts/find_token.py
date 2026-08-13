#!/usr/bin/env python
"""Guess a company's ATS board token by probing name variants.

Board tokens are not published anywhere, and the manual lookup (careers page ->
Apply -> read the URL) is the real cost of adding a company. This resolves the
easy majority automatically; whatever it misses still needs the manual 90
seconds.

    python scripts/find_token.py "Razorpay" "Hasura" "Wiz"
    python scripts/find_token.py --unresolved     # everything the verifier failed on
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "seeds" / "verify_report.json"

UA = {"User-Agent": "job-agent/0.1 (personal job search; contact via repo owner)"}
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}
# Ordered roughly by observed hit rate. Every one of these was learned from a
# token that actually resolved: harnessinc/wizinc (inc), chronospherejobs
# (jobs), razorpaysoftwareprivatelimited (full legal entity name). Indian
# companies in particular register their board under the registered entity,
# which is why the "...privatelimited" forms are worth the extra requests.
SUFFIXES = ["", "inc", "jobs", "hq", "io", "software", "technologies", "labs",
            "careers", "tech", "india", "ai", "com",
            "softwareprivatelimited", "technologiesprivatelimited",
            "privatelimited", "solutionsprivatelimited"]


def candidates(name: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    bases = {
        "".join(words),
        "-".join(words),
        words[0],
        "".join(w for w in words if w not in {"labs", "inc", "technologies", "global", "tech"}),
    }
    out: list[str] = []
    for base in bases:
        if not base:
            continue
        for suf in SUFFIXES:
            tok = base + suf
            if tok not in out:
                out.append(tok)
    return out


def hit(ats: str, token: str):
    """-> (job_count, identity) if this (ats, token) is a live board, else None.

    `identity` is a sample of what the board actually contains. A token guess
    resolving to *a* live board proves nothing about *which* company it belongs
    to: probing "Pine Labs" finds greenhouse/pine, a Canadian mortgage brokerage,
    and "Fractal" finds a US venture studio. Both look like clean hits on job
    count alone, so the caller must be shown enough to reject them.
    """
    try:
        resp = requests.get(ENDPOINTS[ats].format(token=token), headers=UA, timeout=15)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None

    if isinstance(payload, dict):
        if payload.get("error") or payload.get("errors"):
            return None
        jobs = payload.get("jobs", [])
        first = jobs[0] if jobs else {}
        company = first.get("company_name") or ""          # greenhouse only
        loc = first.get("location")
        loc = loc.get("name") if isinstance(loc, dict) else (loc or "")
        title = first.get("title") or ""
    elif isinstance(payload, list):
        jobs = payload
        first = jobs[0] if jobs else {}
        company = ""
        loc = (first.get("categories") or {}).get("location") or ""
        title = first.get("text") or ""
    else:
        return None

    identity = " | ".join(p for p in [company, title[:40], loc[:28]] if p) or "(empty board)"
    return len(jobs), identity


def search(name: str) -> list[tuple[str, str, int, str]]:
    pairs = [(ats, tok) for tok in candidates(name) for ats in ENDPOINTS]
    found: list[tuple[str, str, int, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (ats, tok), result in zip(pairs, pool.map(lambda p: hit(*p), pairs)):
            if result is not None:
                found.append((ats, tok, result[0], result[1]))
    found.sort(key=lambda f: -f[2])
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--unresolved", action="store_true",
                    help="read dead entries out of seeds/verify_report.json")
    args = ap.parse_args()

    names = list(args.names)
    if args.unresolved:
        if not REPORT.exists():
            print("No verify_report.json — run scripts/verify_boards.py first.")
            return 1
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        names += [r["name"] for r in report
                  if r["status"] in ("dead", "error") and not r.get("alternate")]
    if not names:
        ap.print_help()
        return 1

    for name in names:
        results = search(name)
        if not results:
            print(f"{name:<18} no candidate token worked — needs the manual lookup")
            continue
        print(f"{name}")
        for ats, tok, n, identity in results[:3]:
            print(f"    {ats}/{tok}  ({n} jobs)  ->  {identity}")
        print("    ^ CHECK THE IDENTITY before adding — a live board is not "
              "proof it is the right company.")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
