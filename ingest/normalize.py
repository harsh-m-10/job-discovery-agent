"""LAYER 1. HTML stripping, content hashing, and the location gate.

The location gate runs here, before anything reaches Layer 2, so that rejected
jobs never cost an LLM call.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone

from common.geo import REMOTE, india_pattern, is_india_relevant  # noqa: F401

BLOCK_TAGS = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|h[1-6]|section|article|table)\b[^>]*>", re.I
)
ANY_TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\r\f\v]+")
BLANKS = re.compile(r"\n{3,}")

# The location gate moved to common/geo.py in Phase 1.1 so that this module,
# the Workday facet picker and scripts/verify_boards.py stop keeping three
# separate city lists that had already drifted apart. The vocabulary now lives
# in config/settings.yaml.
#
# Re-exported here because `from ingest.normalize import is_india_relevant` is
# the import six modules already use, and because Layer 1 remains the layer that
# owns the gate conceptually even though the matcher is shared.


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


_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")


def is_date_only(value) -> bool:
    """True for an ISO calendar date with no time part ("2026-10-15").

    parse_iso accepts those and returns midnight UTC, which is a correct
    *type* but invents a time of day. Callers use this to record that the
    time is a placeholder (jobs.posted_at_precision = 'date').
    """
    return isinstance(value, str) and bool(_DATE_ONLY.match(value))


def parse_iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
