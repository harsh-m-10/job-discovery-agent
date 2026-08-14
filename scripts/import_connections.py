#!/usr/bin/env python
"""Import the LinkedIn connections export into the `connections` table.

This is a first-party export of the operator's own data, downloaded by hand from
LinkedIn (Settings -> Data Privacy -> Get a copy of your data). Nothing here
scrapes anything; the CSV arrives by email.

The file is PII about other people, so it must live OUTSIDE the repository. The
path comes from CONNECTIONS_CSV_PATH and defaults to the canonical private
directory; a path inside the working tree is refused outright rather than
merely warned about, because a .gitignore rule is one careless `git add -f`
away from failing.

    python scripts/import_connections.py --dry-run
    python scripts/import_connections.py
    CONNECTIONS_CSV_PATH=/some/other/Connections.csv python scripts/import_connections.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from score.referral import normalize_company   # noqa: E402

# Canonical home for PII inputs: outside the repo, outside any sync folder the
# repo lives in, and stable across machines.
DEFAULT_DIR = Path.home() / ".job-agent" / "private"

LINKEDIN_COLUMNS = {"First Name", "Last Name", "URL", "Company", "Position", "Connected On"}


class UnsafePath(RuntimeError):
    pass


def resolve_csv_path(explicit: str | None = None) -> Path:
    """Locate Connections.csv and refuse anything inside the repository."""
    candidate = explicit or os.environ.get("CONNECTIONS_CSV_PATH")

    if candidate:
        path = Path(candidate).expanduser().resolve()
    else:
        # Search the canonical directory, including an unzipped export folder.
        hits = sorted(DEFAULT_DIR.rglob("Connections.csv")) if DEFAULT_DIR.exists() else []
        if not hits:
            raise FileNotFoundError(
                f"No Connections.csv found under {DEFAULT_DIR}.\n"
                f"Put the LinkedIn export there, or set CONNECTIONS_CSV_PATH."
            )
        path = hits[0].resolve()

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    # The guard. Compare resolved paths so symlinks cannot sneak past it.
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return path                      # outside the repo — good
    raise UnsafePath(
        f"\n  REFUSING TO READ PII FROM INSIDE THE REPOSITORY\n"
        f"  file: {path}\n"
        f"  repo: {ROOT.resolve()}\n\n"
        f"  This file contains personal data about other people. A .gitignore\n"
        f"  entry is not sufficient protection — one `git add -f` commits it\n"
        f"  permanently to history.\n\n"
        f"  Move it out and re-run:\n"
        f"    mkdir -p {DEFAULT_DIR}\n"
        f"    mv \"{path}\" {DEFAULT_DIR}/\n"
    )


def parse_connected_on(value: str | None):
    """LinkedIn writes '11 Aug 2026'. Older exports use ISO."""
    if not value or not value.strip():
        return None
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d %B %Y", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def read_rows(path: Path) -> list[dict]:
    """Parse the export, skipping LinkedIn's preamble.

    The Basic archive begins with a 'Notes:' line and a quoted paragraph about
    missing email addresses before the real header, so the header row has to be
    found rather than assumed to be first.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    header_index = next(
        (i for i, line in enumerate(lines)
         if "First Name" in line and "Last Name" in line),
        None,
    )
    if header_index is None:
        raise ValueError(
            "No LinkedIn header row found. Expected a line containing "
            "'First Name,Last Name,...'. Is this really Connections.csv?"
        )

    reader = csv.DictReader(lines[header_index:])
    missing = LINKEDIN_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Connections.csv is missing columns: {sorted(missing)}")
    return list(reader)


def to_record(row: dict) -> dict:
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    company_raw = (row.get("Company") or "").strip()
    connected = parse_connected_on(row.get("Connected On"))
    return {
        "full_name": f"{first} {last}".strip(),
        "company_raw": company_raw or None,
        "company_norm": normalize_company(company_raw) or None,
        "title": (row.get("Position") or "").strip() or None,
        "connected_on": connected.isoformat() if connected else None,
        "profile_url": (row.get("URL") or "").strip() or None,
        "is_batchmate": False,          # set by hand in the dashboard; see spec §7.3
    }


def upload(records: list[dict]) -> None:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}

    # Replace wholesale: the export is a full snapshot, and connections that
    # disappeared should not linger as stale referral suggestions.
    requests.delete(f"{url}/rest/v1/connections?id=gt.0", headers=headers, timeout=60)
    for i in range(0, len(records), 500):
        resp = requests.post(f"{url}/rest/v1/connections", headers=headers,
                             data=json.dumps(records[i:i + 500]), timeout=90)
        if resp.status_code >= 300:
            raise SystemExit(f"insert failed {resp.status_code}: {resp.text[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", help="explicit path to Connections.csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report statistics; write nothing")
    args = ap.parse_args()

    try:
        path = resolve_csv_path(args.path)
    except (UnsafePath, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"reading {path}")
    rows = read_rows(path)
    records = [to_record(r) for r in rows]
    records = [r for r in records if r["full_name"]]

    with_company = [r for r in records if r["company_raw"]]
    normalized = {r["company_norm"] for r in with_company if r["company_norm"]}
    raw_distinct = {r["company_raw"].lower() for r in with_company}

    print(f"\n  rows parsed            {len(records)}")
    print(f"  with a company value   {len(with_company)}"
          f" ({len(with_company) / max(len(records), 1) * 100:.0f}%)")
    print(f"  distinct company_raw   {len(raw_distinct)}")
    print(f"  distinct company_norm  {len(normalized)}")
    collapse = (1 - len(normalized) / len(raw_distinct)) * 100 if raw_distinct else 0
    print(f"  normalization collapse {collapse:.1f}%"
          f"  ({len(raw_distinct) - len(normalized)} variants merged)")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    upload(records)
    print(f"\nimported {len(records)} connections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
