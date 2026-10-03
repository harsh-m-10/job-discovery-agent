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
    """Raised when a board cannot be fetched. Never aborts a run.

    `transient` separates "this board is gone" from "this board is briefly
    unwell", because only the first should ever deactivate a company.

    A 404, a renamed token or a malformed token is permanent: the board will
    not come back on its own and five strikes should retire it. An HTML error
    page, a 5xx or a dropped connection is not. Workday in particular serves
    its SPA shell instead of JSON during a maintenance window that has landed
    between 06:37 and 07:57 UTC on 2026-08-15, 08-22, 09-26 and 10-03, taking
    out every tenant on a host at once and recovering by the next run. Counting
    those four runs the same way as a dead token would have deactivated Adobe,
    Cisco, Target and Visa together, and they would have stayed dark until
    somebody noticed.
    """

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient
