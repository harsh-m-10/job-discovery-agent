"""Shared, layer-free helpers.

`common/` may be imported by any layer and imports none of them. It holds logic
that more than one layer needs to agree on, so that agreement is structural
rather than conventional — see docs/PLAN.md 8.3 and the `common/` rule in
scripts/check_boundaries.py.

Nothing here touches a database or the network.
"""
