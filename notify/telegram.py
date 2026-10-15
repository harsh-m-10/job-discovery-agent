"""Telegram Bot API ping — the instant channel (docs/PLAN.md Phase 3.1).

Why this exists: discovery is now minutes behind a posting, but the measured
median from posting to application is 13.8 hours. The ping is what closes that
gap, so it has to land on the phone, immediately, one job per message. The
email digest remains the fallback and the daily summary.

Free, no phone-number registration dance (CallMeBot needed one and was never
set up: 13,067 failed pings recorded before anyone noticed). Setup: create a
bot with @BotFather, send it one message, and store

    TELEGRAM_BOT_TOKEN   the token BotFather returns
    TELEGRAM_CHAT_ID     your chat id (docs/RUNBOOK.md shows how to read it)

Every failure raises, so notify/run.py can route the job to email instead of
losing it.
"""

from __future__ import annotations

import os

import requests

from notify import whatsapp

API = "https://api.telegram.org"
# Telegram rejects messages over 4096 characters.
MAX_CHARS = 4000


class NotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def format_message(job: dict, dashboard_url: str = "") -> str:
    """Same triage-first layout as every other channel, plus the two lines
    that make a ping actionable from the phone: which resume to send, and
    whether the score is still provisional."""
    lines = [whatsapp.format_message(job, dashboard_url)]
    family = job.get("role_family")
    variant = job.get("resume_variant")
    if family and variant:
        lines.append(f"Resume: {family} -> {variant}")
    elif family:
        lines.append(f"Role family: {family}")
    if job.get("provisional"):
        lines.append("(provisional score — rules only, LLM unavailable)")
    if job.get("absolute_url"):
        lines.append(f"Apply: {job['absolute_url']}")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1] + "…"


def send(message: str, timeout: int = 20) -> None:
    """Raises on any failure so the caller can fall back to email."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise NotConfigured("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set")
    resp = requests.post(
        f"{API}/bot{token}/sendMessage",
        json={"chat_id": chat, "text": message,
              # Plain text on purpose: job titles routinely contain the
              # characters Markdown and HTML modes would need escaped.
              "disable_web_page_preview": True},
        timeout=timeout)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    # The Bot API reports failures as {"ok": false, "description": ...}, and
    # never echoes the token, so the description is safe to record.
    if resp.status_code != 200 or not body.get("ok"):
        raise RuntimeError(f"telegram {resp.status_code}: "
                           f"{body.get('description') or resp.text[:200]}")
