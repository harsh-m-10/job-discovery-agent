#!/usr/bin/env python
"""Dashboard timing report (specs/001-dashboard-speed-retention, FR-001/002).

    python scripts/perf_report.py --label baseline
    python scripts/perf_report.py --label after --base https://<preview>.vercel.app

For each page: three timed loads (TTFB, total, size), the function region from
x-vercel-id, and the per-query breakdown from /api/perf. Writes
specs/001-dashboard-speed-retention/perf-<label>.md. The secret path never
appears in the output.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
OUT_DIR = ROOT / "specs" / "001-dashboard-speed-retention"
DEFAULT_BASE = "https://job-agent-dashboard-three.vercel.app"

PAGES = [("queue", ""), ("funnel", "/funnel"), ("contacts", "/contacts"),
         ("companies", "/companies"), ("review", "/review")]


def timed_get(url: str, headers: dict | None = None) -> tuple[float, float, int, requests.Response]:
    t0 = time.perf_counter()
    r = requests.get(url, headers=headers or {}, timeout=120, stream=True)
    ttfb = time.perf_counter() - t0
    body = r.content
    return ttfb, time.perf_counter() - t0, len(body), r


def region(resp: requests.Response) -> str:
    # x-vercel-id: "<edge>::<function region>::<id>" (edge only for static).
    parts = (resp.headers.get("x-vercel-id") or "").split("::")
    return parts[-2] if len(parts) >= 3 else (parts[0] or "?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    secret = os.environ["DASHBOARD_SECRET"].strip()
    base = args.base.rstrip("/")
    api_h = {"x-dashboard-secret": secret}
    lines = [f"# Dashboard timing: {args.label}", "",
             f"Measured {time.strftime('%Y-%m-%d %H:%M %Z')} from the operator's machine "
             f"(India), {args.runs} loads per page.", "",
             "| page | status | function region | TTFB median (s) | total median (s) | KB |",
             "|---|---|---|---|---|---|"]
    breakdowns = []
    for name, path in PAGES:
        samples = [timed_get(f"{base}/d/{secret}{path}") for _ in range(args.runs)]
        r = samples[-1][3]
        lines.append(f"| {name} | {r.status_code} | {region(r)} | "
                     f"{statistics.median(s[0] for s in samples):.2f} | "
                     f"{statistics.median(s[1] for s in samples):.2f} | {samples[-1][2] // 1024} |")
        if name != "review":
            pr = requests.get(f"{base}/api/perf?page={name}", headers=api_h, timeout=120)
            if pr.ok:
                breakdowns.append((name, pr.json()))
            else:
                breakdowns.append((name, {"error": f"HTTP {pr.status_code}"}))

    lines += ["", "## Per-query breakdown (from /api/perf, server side)", ""]
    for name, data in breakdowns:
        if "error" in data:
            lines += [f"### {name}: {data['error']}", ""]
            continue
        qs = data["queries"]
        lines += [f"### {name}: {data['total_ms']} ms total, {len(qs)} requests, "
                  f"{sum(q['pages'] for q in qs)} round trips", "",
                  "| ms | started at | pages | rows | request |", "|---|---|---|---|---|"]
        for q in sorted(qs, key=lambda q: -q["ms"])[:12]:
            lines.append(f"| {q['ms']} | {q['started']} | {q['pages']} | {q['rows']} | `{q['name']}` |")
        lines.append("")

    out = OUT_DIR / f"perf-{args.label}.md"
    text = "\n".join(lines) + "\n"
    assert secret not in text, "secret leaked into the report"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
