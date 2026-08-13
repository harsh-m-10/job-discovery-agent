"""LAYER 1. Shared shapes. No PII, no personalization — see spec §3 boundary rule."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class RawJob:
    """One posting, normalized out of whatever shape its ATS returned.

    Adapters must not leak ATS-specific structures above this type.
    """

    ats_job_id: str
    title: str
    location: str
    description: str          # plain text, HTML already stripped
    absolute_url: str
    posted_at: datetime | None = None
    compensation: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ATSAdapter(Protocol):
    ats_name: str

    def fetch(self, board_token: str) -> list[RawJob]: ...


class BoardFetchError(RuntimeError):
    """Raised when a board cannot be fetched. Never aborts a run — the caller
    counts it against the company's consecutive_failures."""
