#!/usr/bin/env python
"""Emit the verbatim screening facts for the dashboard.

The dashboard deploys from dashboard/ on Vercel and cannot read the profile at
runtime, so the compensation block has to cross the boundary somehow. It used to
be committed as dashboard/lib/screening.generated.json — that stopped being an
option when this repository went public, because the file is a compact statement
of exactly the facts a public repo must not carry: CTC, employer, notice period.

So it travels as an environment variable instead. This script prints the value;
the dashboard reads it through dashboard/lib/screening.ts. The profile stays the
single source of truth.

    python scripts/sync_screening.py          # write .local/ + print the value
    python scripts/sync_screening.py --print  # just the one-line JSON

Then:

    vercel env add SCREENING_JSON production   # paste the one-line JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from score.llm import profile_path  # noqa: E402

# Untracked: this file holds the same PII as the profile itself.
OUT = ROOT / ".local" / "screening.json"

# Only these keys cross the boundary. Nothing here is ever LLM-generated: a
# hallucinated CTC or notice period is unrecoverable once an employer sees it.
FIELDS = ("current_ctc", "expected_ctc", "notice_period_days",
          "location_preference", "expected_base_min_lpa")


def build() -> dict:
    profile = yaml.safe_load(profile_path().read_text(encoding="utf-8"))
    comp = profile.get("compensation", {})
    identity = profile.get("identity", {})
    return {
        **{key: comp[key] for key in FIELDS if key in comp},
        "years_experience_post_grad": identity.get("years_experience_post_grad"),
        "years_experience_incl_internship": identity.get("years_experience_incl_internship"),
        "current_title": identity.get("current_title"),
        "current_company": identity.get("current_company"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="emit only the one-line JSON, for piping")
    args = ap.parse_args()

    payload = build()
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    if args.print_only:
        print(compact)
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  (untracked)")
    for key in FIELDS:
        if key in payload:
            print(f"  {key}: {payload[key]}")

    missing = [k for k in ("current_ctc", "expected_ctc") if not payload.get(k)]
    placeholder = [k for k, v in payload.items()
                   if isinstance(v, str) and ("TODO" in v or v.startswith("<"))]
    if missing:
        print(f"\nWARNING: still unset: {', '.join(missing)}")
    if placeholder:
        print(f"WARNING: placeholder values present: {', '.join(placeholder)}")

    print("\nSet this as SCREENING_JSON in Vercel:\n")
    print(f"  {compact}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
