#!/usr/bin/env python
"""Discover Workday tenant/site pairs.

Workday exposes the same JSON endpoint every tenant's own careers page calls:

    POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

It is public and unauthenticated, but undocumented: the wd1/wd3/wd5/wd101
prefix differs per tenant, the site name is arbitrary, and neither is published
anywhere. This brute-forces the combination and reports the job count plus a
sample posting so identity can be confirmed — the same rule that caught
greenhouse/pine masquerading as Pine Labs.

    python scripts/find_workday.py walmart target adobe
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

HOSTS = ["wd1", "wd3", "wd5", "wd12", "wd101", "wd103"]

# Site names are arbitrary per tenant and published nowhere. These are the
# observed shapes — Adobe uses "external_experienced", Walmart "WalmartExternal"
# — plus tenant-derived variants generated per company below.
GENERIC_SITES = [
    "External", "external", "Careers", "careers", "External_Careers",
    "ExternalCareerSite", "External_Career_Site", "external_career_site",
    "CareerSite", "Career", "jobs", "Jobs", "Search", "search",
    "GlobalCareers", "Global_Careers", "ExternalJobs", "Professional",
    "external_experienced", "External_Experienced", "experienced",
    "ExternalSite", "External_Site", "Externalcareers", "externalcareers",
    "CareerHub", "Recruiting", "recruiting", "Corporate", "Global",
]


def site_candidates(tenant: str) -> list[str]:
    """Generic names plus the tenant-derived forms Workday customers favour."""
    cap = tenant.capitalize()
    upper = tenant.upper()
    derived = [
        f"{tenant}External", f"{cap}External", f"{upper}External",
        f"{tenant}_External", f"{cap}_External",
        f"{tenant}Careers", f"{cap}Careers", f"{tenant}_Careers",
        f"{cap}_Careers", f"{tenant}careers", f"{tenant}_careers",
        f"{cap}", f"{tenant}", f"{upper}",
        f"{tenant}Jobs", f"{cap}Jobs", f"{tenant}_jobs",
        f"{cap}ExternalCareerSite", f"{tenant}ExternalCareerSite",
        f"{cap}_External_Career_Site",
    ]
    seen, out = set(), []
    for name in derived + GENERIC_SITES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


HEADERS = {
    # Workday fronts these tenants with a bot filter that rejects non-browser
    # agents outright, so a realistic UA is required even though the endpoint
    # itself is public and unauthenticated.
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def probe(tenant: str, host: str, site: str, timeout: int = 12):
    """-> dict on a real board, "host_ok" when the tenant/host pair exists but
    the site name is wrong, else None.

    The status codes are the opposite of the intuitive reading, and getting
    them backwards makes the search silently useless:

        404  the tenant DOES live on this host; the site name is wrong
        422  wrong host — a nonsense tenant returns 422 on every host too
        200 + json   correct tenant, host and site

    Confirming the host first is what makes a two-stage search worthwhile: site
    guesses are only spent where they can possibly succeed.
    """
    url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        resp = requests.post(url, headers=HEADERS, timeout=timeout,
                             data=json.dumps({"appliedFacets": {}, "limit": 20,
                                              "offset": 0, "searchText": ""}))
    except requests.RequestException:
        return None
    if resp.status_code == 404:
        return "host_ok"
    if resp.status_code != 200:
        return None
    if "application/json" not in resp.headers.get("content-type", ""):
        # The SPA shell. The tenant is real, this path is not the API.
        return "host_ok"
    try:
        payload = resp.json()
    except ValueError:
        return None
    postings = payload.get("jobPostings")
    if postings is None:
        return "host_ok" if payload.get("errorCode") else None
    total = payload.get("total", len(postings))
    sample = ""
    if postings:
        first = postings[0]
        sample = f"{(first.get('title') or '')[:44]} | {(first.get('locationsText') or '')[:30]}"
    return {"host": host, "site": site, "total": total, "sample": sample}


def search(tenant: str) -> tuple[list[dict], list[str]]:
    """-> (working boards, hosts that exist but whose site name eluded us)."""
    # Stage 1: which wd host serves this tenant? A junk site name is enough.
    live_hosts: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for host, result in zip(HOSTS, pool.map(
                lambda h: probe(tenant, h, "zzNotARealSite"), HOSTS)):
            if result is not None:
                live_hosts.append(host)

    # Stage 2: guess the site name, but only on hosts that answered.
    found: list[dict] = []
    sites = site_candidates(tenant)
    for host in live_hosts:
        combos = [(host, site) for site in sites]
        with ThreadPoolExecutor(max_workers=10) as pool:
            for result in pool.map(lambda c: probe(tenant, c[0], c[1]), combos):
                if isinstance(result, dict):
                    found.append(result)
    found.sort(key=lambda r: -r["total"])
    return found, live_hosts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tenants", nargs="+", help="tenant slugs, e.g. walmart adobe")
    args = ap.parse_args()

    for tenant in args.tenants:
        results, live_hosts = search(tenant)
        if not results:
            if live_hosts:
                print(f"{tenant:<14} tenant exists on {', '.join(live_hosts)} but the "
                      f"site name was not guessed — read it off the careers page URL")
            else:
                print(f"{tenant:<14} no Workday tenant under this slug")
            sys.stdout.flush()
            continue
        print(f"{tenant}")
        for r in results[:4]:
            print(f"    {r['host']}/{r['site']:<26} {r['total']:>6} jobs   {r['sample']}")
        print(f"    -> token: {tenant}|{results[0]['host']}|{results[0]['site']}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
