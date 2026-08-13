#!/usr/bin/env python
"""Enforce the Layer 1 / Layer 2 separation (spec §3, acceptance §14).

Layer 1 stays publishable as a PII-free public job board. That only remains true
if it never learns about the operator: no imports from score/ or notify/, and no
reference to the personalization tables.

Also asserts the hard constraint that no banned job platform is contacted from
anywhere in the codebase.

    python scripts/check_boundaries.py
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LAYER1 = ROOT / "ingest"
FORBIDDEN_IN_LAYER1 = re.compile(r"\b(connections|applications|job_scores)\b")
FORBIDDEN_IMPORTS = re.compile(r"^\s*(?:from|import)\s+(score|notify)\b", re.M)

# Spec §2: scraping any of these risks the account the referral channel depends
# on, so the constraint is checked mechanically rather than trusted to memory.
# Matched only in URL position — the codebase legitimately discusses LinkedIn in
# prose, since the connections CSV is a first-party export from it.
BANNED_HOSTS = re.compile(
    r"https?://[^\s\"'<>]*?(linkedin\.com|naukri\.com|indeed\.com|glassdoor\.)", re.I
)

SKIP_DIRS = {".venv", "node_modules", ".next", "__pycache__", ".git", "scratchpad"}


def py_files(root: Path):
    for path in root.rglob("*.py"):
        if not SKIP_DIRS & set(path.parts):
            yield path


def all_source():
    for pattern in ("*.py", "*.ts", "*.tsx", "*.js", "*.yml", "*.yaml"):
        for path in ROOT.rglob(pattern):
            if not SKIP_DIRS & set(path.parts):
                yield path


def code_tokens(text: str):
    """Yield (line, token_text) for real code only.

    Comments and docstrings are skipped: Layer 1 files are expected to *describe*
    the boundary rule, and naming a forbidden table in prose is not a violation.
    String literals count as code, though — that is where a table name in a
    PostgREST path would actually live.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    prev_meaningful = tokenize.INDENT
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            # A bare string expression is a docstring; one used as a value is not.
            if prev_meaningful in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                                   tokenize.DEDENT, tokenize.ENCODING):
                prev_meaningful = tok.type
                continue
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT):
            yield tok.start[0], tok.string
        prev_meaningful = tok.type


def main() -> int:
    failures: list[str] = []

    for path in py_files(LAYER1):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for line, token in code_tokens(text):
            match = FORBIDDEN_IN_LAYER1.search(token)
            if match:
                failures.append(
                    f"{rel}:{line}: Layer 1 references Layer 2 table {match.group(1)!r}"
                )
        for match in FORBIDDEN_IMPORTS.finditer(text):
            line = text[:match.start()].count("\n") + 1
            failures.append(f"{rel}:{line}: Layer 1 imports from {match.group(1)}/")

    for path in all_source():
        # This checker names the banned hosts by definition; exempt itself.
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in BANNED_HOSTS.finditer(text):
            line = text[:match.start()].count("\n") + 1
            failures.append(
                f"{path.relative_to(ROOT)}:{line}: banned platform reference "
                f"{match.group(0)!r} (spec §2)"
            )

    if failures:
        print("BOUNDARY VIOLATIONS\n")
        for f in failures:
            print(f"  {f}")
        return 1
    print("boundaries ok: Layer 1 is Layer-2-free, no banned platform is contacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
