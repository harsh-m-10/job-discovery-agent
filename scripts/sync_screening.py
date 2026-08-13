#!/usr/bin/env python
"""Copy the verbatim screening facts into the dashboard bundle.

The dashboard deploys from dashboard/ on Vercel and cannot read ../config at
runtime, so the compensation block is materialised into a generated JSON file.
config/candidate_profile.yaml stays the single source of truth — this file is
generated, never hand-edited, exactly like seeds/companies.verified.yaml.

Re-run after changing any compensation value:

    python scripts/sync_screening.py
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "candidate_profile.yaml"
OUT = ROOT / "dashboard" / "lib" / "screening.generated.json"

# Only these keys cross the boundary. Nothing here is ever LLM-generated: a
# hallucinated CTC or notice period is unrecoverable once an employer sees it.
FIELDS = ("current_ctc", "expected_ctc", "notice_period_days",
          "location_preference", "expected_base_min_lpa")


def main() -> int:
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    comp = profile.get("compensation", {})
    identity = profile.get("identity", {})

    payload = {
        "_generated_by": "scripts/sync_screening.py — do not edit by hand",
        "_source": "config/candidate_profile.yaml",
        **{key: comp[key] for key in FIELDS if key in comp},
        "years_experience_post_grad": identity.get("years_experience_post_grad"),
        "years_experience_incl_internship": identity.get("years_experience_incl_internship"),
        "current_title": identity.get("current_title"),
        "current_company": identity.get("current_company"),
    }

    missing = [k for k in ("current_ctc", "expected_ctc") if not payload.get(k)]
    placeholder = [k for k, v in payload.items()
                   if isinstance(v, str) and "TODO" in v or
                   (isinstance(v, str) and v.startswith("<"))]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    print(f"wrote {OUT.relative_to(ROOT)}")
    for key in FIELDS:
        if key in payload:
            print(f"  {key}: {payload[key]}")
    if missing:
        print(f"\nWARNING: still unset: {', '.join(missing)}")
    if placeholder:
        print(f"WARNING: placeholder values present: {', '.join(placeholder)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
