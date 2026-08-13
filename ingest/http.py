"""LAYER 1. One shared HTTP session with sane retries for all adapters."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import BoardFetchError

UA = "job-agent/0.1 (personal job search; low volume)"

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        retry = Retry(
            total=2,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        _session = s
    return _session


def get_json(url: str, timeout: int = 20):
    try:
        resp = session().get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise BoardFetchError(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 404:
        raise BoardFetchError("board not found (404) — token may have changed")
    if resp.status_code != 200:
        raise BoardFetchError(f"http {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise BoardFetchError("non-json response") from exc
