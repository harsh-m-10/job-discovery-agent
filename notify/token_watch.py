"""LAYER 3. Warn before the dispatch token expires (Phase 2.6).

From Phase 2 the pipeline is triggered by an external pinger and a Cloudflare
standby, both of which call the GitHub API with one fine-grained personal
access token. Fine-grained tokens expire. When this one does, both triggers
start getting 401s at the same moment, nothing runs, and the only signal is
the healthchecks.io heartbeat going quiet afterwards. This module turns that
into a warning two weeks ahead.

GitHub reports a token's expiry on every authenticated response in the
`github-authentication-token-expiration` header, so one cheap GET is enough.
The watchdog calls `check()` on every run; the alert path's own cooldown keeps
that to one email a day.

Needs the same token stored as the GH_DISPATCH_PAT Actions secret. Without it
this reports "not configured" and stays out of the way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

REPO = "harsh-m-10/job-discovery-agent"
EXPIRY_HEADER = "github-authentication-token-expiration"
WARN_DAYS = (14, 3)          # first warning, then the urgent one
TIMEOUT_S = 15


@dataclass
class TokenStatus:
    state: str               # unconfigured | ok | expiring | invalid | unknown
    detail: str
    days_left: float | None = None


def parse_expiry(value: str | None) -> datetime | None:
    """Parse GitHub's expiry header tolerantly.

    Accepts both "2026-11-14 12:00:00 UTC" and "2026-11-14 12:00:00 -0800".
    Not yet observed on this project's own token -- it does not exist until
    Phase 2's setup -- so both plausible forms are handled rather than one
    being assumed. An unparseable value reads as "no expiry" (ok), which is the
    safe side only because the 401 path still catches a token that has lapsed.
    """
    if not value:
        return None
    text = value.strip()
    for fmt, src in (("%Y-%m-%d %H:%M:%S %z", text),
                     ("%Y-%m-%d %H:%M:%S", text.removesuffix(" UTC"))):
        try:
            parsed = datetime.strptime(src, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def classify(status_code: int, expiry_header: str | None,
             now: datetime | None = None) -> TokenStatus:
    """Pure: turn one API response into a verdict. Tested without the network."""
    now = now or datetime.now(timezone.utc)
    if status_code == 401:
        return TokenStatus("invalid",
                           "GitHub rejected the dispatch token (401) — it has "
                           "expired or been revoked, so the external triggers "
                           "are failing right now")
    if status_code >= 400:
        return TokenStatus("unknown", f"GitHub answered {status_code}; could not "
                                      f"read the token's expiry")
    expires = parse_expiry(expiry_header)
    if expires is None:
        # A token created with "no expiration" sends no header at all.
        return TokenStatus("ok", "token has no expiry date")
    days = (expires - now).total_seconds() / 86400
    if days <= WARN_DAYS[0]:
        return TokenStatus("expiring",
                           f"dispatch token expires {expires:%Y-%m-%d} "
                           f"({days:.1f} days)", days)
    return TokenStatus("ok", f"dispatch token valid until {expires:%Y-%m-%d} "
                             f"({days:.0f} days)", days)


def check() -> TokenStatus:
    """One authenticated GET. Never raises."""
    token = (os.environ.get("GH_DISPATCH_PAT") or "").strip()
    if not token:
        return TokenStatus("unconfigured",
                           "GH_DISPATCH_PAT not set — token expiry not watched")
    try:
        resp = requests.get(f"https://api.github.com/repos/{REPO}",
                            headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"},
                            timeout=TIMEOUT_S)
    except Exception as exc:
        return TokenStatus("unknown", f"could not reach GitHub: {type(exc).__name__}")
    return classify(resp.status_code, resp.headers.get(EXPIRY_HEADER))


def alert_body(status: TokenStatus) -> str:
    urgent = status.state == "invalid" or (
        status.days_left is not None and status.days_left <= WARN_DAYS[1])
    return "\n".join([
        status.detail + ".",
        "",
        ("Act now: both external triggers stop working when it lapses, and the "
         "only fallback is GitHub's throttled schedule (~5h apart)."
         if urgent else
         "Both external triggers (cron-job.org and the Cloudflare standby) use "
         "this token, so they stop together when it lapses."),
        "",
        "Rotate it:",
        "  1. github.com/settings/personal-access-tokens -> regenerate, scoped to",
        f"     {REPO} with Actions: read & write",
        "  2. update it in three places: the cron-job.org job's Authorization",
        "     header, the Cloudflare Worker secret (wrangler secret put",
        "     GH_DISPATCH_PAT), and this repo's GH_DISPATCH_PAT Actions secret",
        "",
        "Steps are in docs/RUNBOOK.md under 'Rotate the dispatch token'.",
    ])
