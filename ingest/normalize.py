"""LAYER 1. HTML stripping, content hashing, and the location gate.

The location gate runs here, before anything reaches Layer 2, so that rejected
jobs never cost an LLM call.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone

BLOCK_TAGS = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|h[1-6]|section|article|table)\b[^>]*>", re.I
)
ANY_TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\r\f\v]+")
BLANKS = re.compile(r"\n{3,}")

# Kept: India-located, or a remote posting that names India.
# "Remote" on its own is not enough — most of it is Remote-US.
# Note: no bare "IN" country code here — under re.I it would match the English
# word "in" and let every remote-US posting through. Lever's country field is
# checked separately in its adapter.
INDIA = re.compile(
    r"\bbengaluru\b|\bbangalore\b|\bkarnataka\b|\bindia\b|\bhyderabad\b|\bgurgaon\b|\bgurugram\b|\bpune\b|\bnoida\b|\bchennai\b|\bmumbai\b",
    re.I,
)
REMOTE = re.compile(r"\bremote\b|\banywhere\b|\bworldwide\b|\bglobal\b", re.I)


def html_to_text(raw: str | None) -> str:
    """Strip markup to plain text, preserving line structure.

    Greenhouse serves the JD HTML-escaped, so entities are unescaped both before
    and after tag removal. Bullet structure is kept because requirement lists are
    exactly what the experience parser and the LLM need to read.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = BLOCK_TAGS.sub("\n", text)
    text = ANY_TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANKS.sub("\n\n", text).strip()


def clean_text(text: str | None) -> str:
    """Whitespace normalization for fields that arrive as plain text already.

    Ashby and Lever serve pre-rendered plain text, which skips html_to_text and
    so keeps its non-breaking spaces. Running everything through one cleaner
    keeps content hashes comparable no matter which path produced the string.
    """
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\r\n", "\n")
    text = WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANKS.sub("\n\n", text).strip()


def content_hash(title: str, location: str, description: str) -> str:
    payload = f"{title.strip()}|{location.strip()}|{description.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_india_relevant(location: str | None, description: str = "") -> bool:
    """Layer 1 location gate (spec §6.2).

    Postings with no location string at all are kept: several boards omit it and
    the description usually names the office. The Phase 2 prefilter sees the
    description and can still drop them.
    """
    loc = (location or "").strip()
    if not loc:
        return True
    if INDIA.search(loc):
        return True
    if REMOTE.search(loc) and INDIA.search(f"{loc} {description[:1500]}"):
        return True
    return False


def truncate_for_llm(text: str, limit: int) -> str:
    """Head + tail. Requirements cluster at both ends of a JD; the middle is
    usually company boilerplate."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head - 20
    return f"{text[:head]}\n\n[...]\n\n{text[-tail:]}"


def parse_epoch_ms(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
