"""LAYER 1 — ingestion.

Shared, PII-free, cheap at any scale. This package must never import from
`score/` or `notify/`, and must never touch the `connections`, `applications`, or
`job_scores` tables (spec §3). scripts/check_boundaries.py enforces it.
"""
