"""Email fallback. Resend if a key is present, otherwise SMTP.

This path exists because CallMeBot is a free hobby service that can disappear
without warning. It is the safety net, so it deliberately supports two
transports and reports clearly when neither is configured.

Env, either:
    RESEND_API_KEY, ALERT_EMAIL_TO, ALERT_EMAIL_FROM
or:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import requests


class NotConfigured(RuntimeError):
    pass


def transport() -> str | None:
    if os.environ.get("RESEND_API_KEY") and os.environ.get("ALERT_EMAIL_TO"):
        return "resend"
    if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS") \
            and os.environ.get("ALERT_EMAIL_TO"):
        return "smtp"
    return None


def send(subject: str, body: str, timeout: int = 30) -> str:
    """-> transport used. Raises NotConfigured if neither is available."""
    kind = transport()
    to_addr = os.environ.get("ALERT_EMAIL_TO", "")

    if kind == "resend":
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                     "Content-Type": "application/json"},
            json={
                "from": os.environ.get("ALERT_EMAIL_FROM", "onboarding@resend.dev"),
                "to": [to_addr],
                "subject": subject,
                "text": body,
            },
            timeout=timeout,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"resend {resp.status_code}: {resp.text[:300]}")
        return "resend"

    if kind == "smtp":
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = os.environ.get("ALERT_EMAIL_FROM", os.environ["SMTP_USER"])
        message["To"] = to_addr
        message.set_content(body)

        host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.starttls()
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            server.send_message(message)
        return "smtp"

    raise NotConfigured(
        "no email transport configured — set RESEND_API_KEY + ALERT_EMAIL_TO, "
        "or SMTP_USER + SMTP_PASS + ALERT_EMAIL_TO"
    )
