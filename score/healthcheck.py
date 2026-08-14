"""Startup healthcheck for LLM providers.

Two failures this exists to catch before a run wastes twenty minutes:

1. **A dead key or an unpaid account.** Cerebras answers 402 on every model, so
   a chain that looks three deep is really one deep. Better to know at second
   zero than after the lead provider hits its daily cap.
2. **Model rot.** Provider model ids are not stable. `gemini-2.0-flash` and
   `gemini-2.5-flash` already return 404 "no longer available" on this key, and
   `gemini-3-flash-preview` is explicitly a preview that will vanish without
   notice. The availability check names the problem instead of letting it
   surface as an opaque 404 mid-run.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

from .providers import ChatProvider, PaymentRequired, ProviderError

log = logging.getLogger("score.healthcheck")

# Endpoints that list available models, per vendor base URL fragment.
MODEL_LIST_URLS = {
    "generativelanguage.googleapis.com": "https://generativelanguage.googleapis.com/v1beta/models",
    "api.groq.com": "https://api.groq.com/openai/v1/models",
    "api.cerebras.ai": "https://api.cerebras.ai/v1/models",
}


@dataclass
class Health:
    name: str
    model: str
    live: bool
    detail: str
    model_listed: bool | None = None   # None when the vendor was not queryable

    @property
    def label(self) -> str:
        if self.live:
            warn = "" if self.model_listed is not False else "  [MODEL NOT LISTED]"
            return f"live{warn}"
        return f"DOWN — {self.detail}"


def _list_models(provider: ChatProvider) -> list[str] | None:
    """-> available model ids, or None if the vendor could not be asked."""
    key = os.environ.get(provider.cfg.api_key_env)
    if not key:
        return None
    url = next((u for frag, u in MODEL_LIST_URLS.items()
                if frag in provider.cfg.base_url), None)
    if not url:
        return None
    try:
        if "generativelanguage" in url:
            resp = requests.get(f"{url}?key={key}", timeout=20)
            if resp.status_code != 200:
                return None
            return [m["name"].split("/")[-1] for m in resp.json().get("models", [])]
        resp = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if resp.status_code != 200:
            return None
        return [m.get("id") for m in resp.json().get("data", [])]
    except requests.RequestException:
        return None


def check(provider: ChatProvider) -> Health:
    """Minimal live request plus a model-availability lookup."""
    cfg = provider.cfg
    if not provider.available():
        reason = ("disabled in settings" if not cfg.enabled
                  else f"{cfg.api_key_env} not set")
        return Health(provider.name, cfg.model, False, reason)

    listed = _list_models(provider)
    model_listed = None if listed is None else (cfg.model in listed)

    try:
        # 64 not 16: some models emit a preamble and would otherwise hit the
        # cap before producing any content, which reads as a false outage.
        provider.complete("Reply with compact JSON.",
                          'Return exactly {"ok":1}', max_output=64)
        return Health(provider.name, cfg.model, True, "ok", model_listed)
    except PaymentRequired:
        return Health(provider.name, cfg.model, False,
                      "402 payment required — account has no usable quota",
                      model_listed)
    except ProviderError as exc:
        return Health(provider.name, cfg.model, False, str(exc)[:110], model_listed)


def run(providers: list[ChatProvider], fail_fast: bool = True) -> list[Health]:
    """Check every provider, log the result, and raise if none are usable."""
    results = [check(p) for p in providers]

    print("provider healthcheck:")
    for h in results:
        print(f"  {h.name:<15} {h.model:<26} {h.label}")
        if h.model_listed is False:
            log.error(
                "provider %s is configured for model %r, which the vendor does "
                "not list. Model ids rot — check the vendor's model list.",
                h.name, h.model)

    live = [h for h in results if h.live]
    if not live and fail_fast:
        detail = "; ".join(f"{h.name}: {h.detail}" for h in results)
        log.error("NO LLM PROVIDER IS USABLE — %s", detail)
        raise RuntimeError(
            "No LLM provider is usable. Nothing can be scored.\n  " + detail
        )
    if len(live) == 1:
        log.warning("only one provider is live (%s) — no failover headroom",
                    live[0].name)
    return results


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    from score.llm import default_providers

    try:
        run(default_providers(), fail_fast=True)
    except RuntimeError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(0)
