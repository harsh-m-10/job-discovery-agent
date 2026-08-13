"""LAYER 1. Adapter registry."""

from __future__ import annotations

from ..models import ATSAdapter
from .ashby import AshbyAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "workday": WorkdayAdapter(),
}


def get_adapter(ats: str) -> ATSAdapter:
    try:
        return ADAPTERS[ats]
    except KeyError:
        raise ValueError(f"no adapter for ats={ats!r}") from None
