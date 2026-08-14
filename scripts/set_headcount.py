#!/usr/bin/env python
"""Seed companies.headcount_band from seeds/headcount.yaml.

No ATS response carries headcount, so company size is tagged by hand. This
script generates the checklist on first run and applies it thereafter.

    python scripts/set_headcount.py --generate   # write the checklist to fill in
    python scripts/set_headcount.py --dry-run    # show what would change
    python scripts/set_headcount.py              # apply

Bands: micro <25 | small 25-100 | mid 100-1000 | large 1000+ | unknown.
Only `micro` carries a scoring penalty (config/settings.yaml:headcount_penalties),
so leaving a company `unknown` is always safe — it is never silently buried.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECKLIST = ROOT / "seeds" / "headcount.yaml"
VALID = ("micro", "small", "mid", "large", "unknown")

HEADER = """# seeds/headcount.yaml
#
# Company size, tagged by hand — no ATS exposes headcount.
#
#   micro    <25 employees   -> scored -2.0
#   small    25-100          -> neutral
#   mid      100-1000        -> neutral
#   large    1000+           -> neutral
#   unknown                  -> neutral (safe default)
#
# Only `micro` is penalised, so an untagged company is never buried. Edit the
# `band:` values below, then run: python scripts/set_headcount.py
#
# A rough guide for the judgement call: if the company has raised a Series B or
# later, or has a named India office, it is almost certainly mid or large.
"""


def db():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return url, {"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"}


def fetch_companies() -> list[dict]:
    url, headers = db()
    resp = requests.get(
        f"{url}/rest/v1/companies"
        "?select=id,name,ats,category,headcount_band&order=name.asc",
        headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def generate() -> int:
    companies = fetch_companies()
    existing = {}
    if CHECKLIST.exists():
        existing = {e["name"]: e.get("band", "unknown")
                    for e in (yaml.safe_load(CHECKLIST.read_text(encoding="utf-8")) or [])}

    lines = [HEADER]
    for c in companies:
        band = existing.get(c["name"]) or c.get("headcount_band") or "unknown"
        pad = " " * max(1, 24 - len(c["name"]))
        lines.append(
            f"- {{name: {c['name']},{pad}band: {band:<8}}}"
            f"  # {c['ats']}, {c.get('category') or '-'}"
        )
    CHECKLIST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote checklist for {len(companies)} boards -> "
          f"{CHECKLIST.relative_to(ROOT)}")
    print("Edit the band values, then run: python scripts/set_headcount.py")
    return 0


def apply(dry_run: bool) -> int:
    if not CHECKLIST.exists():
        print(f"{CHECKLIST.relative_to(ROOT)} does not exist — run --generate first")
        return 1

    entries = yaml.safe_load(CHECKLIST.read_text(encoding="utf-8")) or []
    wanted = {}
    for entry in entries:
        band = str(entry.get("band", "unknown")).strip().lower()
        if band not in VALID:
            print(f"  invalid band {band!r} for {entry.get('name')!r}; "
                  f"expected one of {VALID}")
            return 1
        wanted[entry["name"]] = band

    url, headers = db()
    companies = fetch_companies()
    changes = [(c, wanted[c["name"]]) for c in companies
               if c["name"] in wanted
               and wanted[c["name"]] != (c.get("headcount_band") or "unknown")]

    if not changes:
        print("no changes — every board already carries its tagged band")
        return 0

    for company, band in changes:
        print(f"  {company['name']:<20} "
              f"{company.get('headcount_band') or 'unknown':<8} -> {band}")
        if dry_run:
            continue
        resp = requests.patch(
            f"{url}/rest/v1/companies?id=eq.{company['id']}", headers=headers,
            data=json.dumps({"headcount_band": band}), timeout=30)
        if resp.status_code >= 300:
            print(f"    failed: {resp.status_code} {resp.text[:200]}")
            return 1

    untagged = [c["name"] for c in companies if c["name"] not in wanted]
    print(f"\n{'would update' if dry_run else 'updated'} {len(changes)} board(s)")
    if untagged:
        print(f"not in the checklist (left unknown, no penalty): "
              f"{', '.join(untagged)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true",
                    help="write seeds/headcount.yaml from the live board list")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return generate() if args.generate else apply(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
