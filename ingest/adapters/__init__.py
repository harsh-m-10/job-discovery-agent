"""LAYER 1. Adapter registry."""

from __future__ import annotations

from ..models import ATSAdapter
from .ashby import AshbyAdapter
from .atlassian import AtlassianAdapter
from .gem import GemAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .oracle import OracleAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .successfactors import SuccessFactorsAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "workday": WorkdayAdapter(),
    "smartrecruiters": SmartRecruitersAdapter(),
    "successfactors": SuccessFactorsAdapter(),
    "gem": GemAdapter(),
    "oracle": OracleAdapter(),
    "atlassian": AtlassianAdapter(),
}


def get_adapter(ats: str) -> ATSAdapter:
    try:
        return ADAPTERS[ats]
    except KeyError:
        raise ValueError(f"no adapter for ats={ats!r}") from None
