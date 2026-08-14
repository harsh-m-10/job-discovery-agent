"""LAYER 2 — LLM provider abstraction.

One request/response contract, several vendors behind it, chosen by config.

Why this exists: Groq's free-tier limits are per *account*, not per key, so a
second project sharing the account competes for the same tokens-per-minute and
tokens-per-day budget. Spreading load across independent vendors is the only
fix that does not involve paying, and it also removes the single point of
failure that took scoring down repeatedly.

All three vendors expose an OpenAI-compatible `/chat/completions`, so one
implementation covers them; only the base URL, model id, key and rate limits
differ, and all of those come from config/settings.yaml. Nothing here hardcodes
a limit.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests


class ProviderError(RuntimeError):
    """Base for anything that should make the caller try the next provider."""


class RequestTooLarge(ProviderError):
    """Payload exceeds this provider's per-request or per-minute ceiling."""


class RateLimited(ProviderError):
    """Temporarily out of budget. Carries the vendor's own retry hint."""

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(f"rate limited (retry in {retry_after:.0f}s): {message}")
        self.retry_after = retry_after


class QuotaExhausted(ProviderError):
    """Daily/period budget gone. Waiting inside a run will not recover it."""

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(f"quota exhausted: {message}")
        self.retry_after = retry_after


class NotConfigured(ProviderError):
    """No API key present for this provider."""


class ServerError(ProviderError):
    """5xx — the vendor is briefly unwell, not out of budget.

    Retried in place with exponential backoff before failing over, because
    "model is overloaded" clears in seconds and burning the failover on it
    would push every batch onto a worse provider for no reason.
    """


class PaymentRequired(ProviderError):
    """The key is valid but the account has no usable quota (HTTP 402).

    Distinct from a rate limit: no amount of waiting helps, and unlike a bad key
    it is not a configuration mistake. Worth its own class so the operator sees
    'add billing' rather than a generic failure.
    """


# Vendors phrase the same condition differently; these are matched against the
# response body, which is the only place the distinction is actually stated.
DAILY_MARKERS = ("tokens per day", "(tpd)", "requests per day", "(rpd)",
                 "quota exceeded", "resource_exhausted", "daily limit")
TOO_LARGE_MARKERS = ("request too large", "context length", "too many tokens",
                     "maximum context", "input is too long")

RETRY_HINT = re.compile(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", re.I)
RETRY_SECONDS = re.compile(r'"?retryDelay"?[:=]\s*"?(\d+(?:\.\d+)?)s', re.I)


def _retry_hint_seconds(body: str) -> float:
    """Pull a delay out of a vendor error body.

    Groq writes 'Please try again in 33m40.032s'; Google returns a
    `retryDelay: "27s"` field. The per-minute reset *header* is not a
    substitute — reading it when the real failure was a daily quota is what
    burned an entire day's budget once already.
    """
    match = RETRY_HINT.search(body or "")
    if match:
        return float(match.group(1) or 0) * 60 + float(match.group(2))
    match = RETRY_SECONDS.search(body or "")
    if match:
        return float(match.group(1))
    return 0.0


@dataclass
class ProviderConfig:
    name: str
    model: str
    base_url: str
    api_key_env: str
    priority: int = 99
    rpm: int = 0                 # 0 = unspecified, no client-side pacing
    tpm: int = 0
    batch_size: int = 4
    max_chars: int = 2000
    max_output_tokens_per_job: int = 320
    json_mode: bool = True
    enabled: bool = True
    timeout: int = 180
    extra_headers: dict[str, str] = field(default_factory=dict)


class Pacer:
    """Per-provider client-side throttle.

    Two mechanisms: a request-per-minute spacer derived from config, and a
    token budget learned from response headers when the vendor sends them.
    Config is authoritative for RPM because not every vendor reports it.
    """

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self._last_call = 0.0
        self.remaining_tokens: float | None = None
        self.reset_after = 0.0

    @staticmethod
    def _seconds(value: str | None) -> float:
        if not value:
            return 0.0
        match = re.match(r"^([\d.]+)\s*(ms|m|s)?$", str(value).strip())
        if not match:
            return 0.0
        amount = float(match.group(1))
        unit = match.group(2) or "s"
        return amount / 1000 if unit == "ms" else amount * 60 if unit == "m" else amount

    def before(self, estimated_tokens: int) -> None:
        if self.cfg.rpm:
            min_gap = 60.0 / self.cfg.rpm
            elapsed = time.monotonic() - self._last_call
            if self._last_call and elapsed < min_gap:
                time.sleep(min_gap - elapsed)

        if (self.cfg.tpm and self.remaining_tokens is not None
                and self.remaining_tokens < estimated_tokens):
            delay = min(max(self.reset_after, 1.0) + 1.0, 65.0)
            print(f"    [{self.cfg.name}] token budget low "
                  f"({self.remaining_tokens:.0f} left, need ~{estimated_tokens}) "
                  f"— waiting {delay:.0f}s")
            time.sleep(delay)
            self.remaining_tokens = None

    def after(self, headers) -> None:
        self._last_call = time.monotonic()
        try:
            self.remaining_tokens = float(headers.get("x-ratelimit-remaining-tokens"))
        except (TypeError, ValueError):
            self.remaining_tokens = None
        self.reset_after = self._seconds(headers.get("x-ratelimit-reset-tokens"))


class ChatProvider:
    """OpenAI-compatible chat completion.

    Google AI Studio, Groq and Cerebras all expose this shape, so vendor
    differences reduce to configuration rather than separate client code.
    """

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.pacer = Pacer(cfg)

    @property
    def name(self) -> str:
        return self.cfg.name

    def available(self) -> bool:
        return self.cfg.enabled and bool(os.environ.get(self.cfg.api_key_env))

    def complete(self, system: str, user: str, max_output: int) -> str:
        key = os.environ.get(self.cfg.api_key_env)
        if not key:
            raise NotConfigured(f"{self.cfg.api_key_env} is not set")

        estimated = (len(system) + len(user)) // 4 + max_output
        self.pacer.before(estimated)

        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": max_output,
        }
        if self.cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = requests.post(
                f"{self.cfg.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         **self.cfg.extra_headers},
                json=payload, timeout=self.cfg.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Transient: an overloaded endpoint stops responding rather than
            # returning 503. Same treatment as a 5xx — retry, then fail over.
            raise ServerError(f"{self.cfg.name} {type(exc).__name__}") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        self.pacer.after(resp.headers)
        body = resp.text[:400]
        lowered = body.lower()

        if resp.status_code == 402:
            raise PaymentRequired(f"{self.cfg.name}: {body}")
        if resp.status_code == 413:
            raise RequestTooLarge(body)
        if resp.status_code == 429:
            if any(m in lowered for m in TOO_LARGE_MARKERS):
                raise RequestTooLarge(body)
            hint = _retry_hint_seconds(body)
            if any(m in lowered for m in DAILY_MARKERS):
                raise QuotaExhausted(hint, body)
            raise RateLimited(min(hint + 2.0, 90.0) or 30.0, body)
        if resp.status_code == 400 and any(m in lowered for m in TOO_LARGE_MARKERS):
            raise RequestTooLarge(body)
        if resp.status_code >= 500:
            raise ServerError(f"{self.cfg.name} {resp.status_code}: {body}")
        if resp.status_code >= 300:
            raise ProviderError(f"{self.cfg.name} {resp.status_code}: {body}")

        try:
            data = resp.json()
            # `.get` not `[...]`: a reply truncated by max_tokens comes back as
            # a valid envelope with finish_reason="length" and no content key.
            # That is a live provider, not a broken one — the caller decides
            # whether an empty completion is usable.
            return data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError(f"unparseable envelope: {body}") from exc


def build_providers(settings: dict) -> list[ChatProvider]:
    """Construct providers from config/settings.yaml, priority order first."""
    raw = settings.get("llm_providers") or []
    configs = [ProviderConfig(**entry) for entry in raw]
    configs.sort(key=lambda c: c.priority)
    return [ChatProvider(c) for c in configs]
