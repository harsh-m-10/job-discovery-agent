"""config/settings.yaml, loaded one way for every layer (docs/PLAN.md 8.3).

    from common.settings import load_settings

There were three identical load_settings functions (ingest/run.py,
score/llm.py, score/run.py) plus ad-hoc yaml reads in notify/run.py. They
agreed only because nobody had changed one yet.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def load_settings(path: Path | None = None) -> dict:
    with (path or SETTINGS).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
