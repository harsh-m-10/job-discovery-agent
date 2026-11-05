"""dashboard_snapshot.data matches specs/001-dashboard-speed-retention/contracts.

    python tests/test_snapshot_contract.py postgresql://.../source

Runs refresh_dashboard_snapshot() on the throwaway CI database (every
migration plus tests/fixtures/backup_seed.sql) inside a rolled-back
transaction and checks every key the dashboard reads.
"""

import sys

import psycopg2

EXPECTED = {
    "version": int, "threshold": (int, float), "max_age_days": int,
    "open_jobs": int, "unscored": int, "stranded": int, "llm_scored": int,
    "hidden_by_age": int, "queue": dict, "new_24h": dict, "week": dict,
    "applied_total": int, "runs_24h": int, "inactive_boards": list,
    "providers_7d": list, "review_pending": int, "health": dict,
    # v2 (0029, spec 002)
    "in_progress": int, "ops": dict,
}
NESTED = {
    "queue": {"total": int, "by_family": dict},
    "new_24h": {"total": int, "by_family": dict, "by_source": dict},
    "week": {"applied": int, "responses": int, "referral_asks": int},
    "health": {"level": str, "reasons": list, "live_providers_24h": int},
}


def main() -> int:
    conn = psycopg2.connect(sys.argv[1])
    bad = []
    try:
        with conn.cursor() as cur:
            cur.execute("select refresh_dashboard_snapshot()")
            data = cur.fetchone()[0]
            cur.execute("select data = %s::jsonb, computed_at > now() - interval '1 minute' "
                        "from dashboard_snapshot where id = 1", (psycopg2.extras.Json(data),))
            stored = cur.fetchone()
            if stored != (True, True):
                bad.append(f"snapshot row not written as returned: {stored}")
    finally:
        conn.rollback()
        conn.close()
    for key, typ in EXPECTED.items():
        if not isinstance(data.get(key), typ):
            bad.append(f"{key}: {type(data.get(key)).__name__}")
    for key, fields in NESTED.items():
        for sub, typ in fields.items():
            if not isinstance((data.get(key) or {}).get(sub), typ):
                bad.append(f"{key}.{sub}: {type((data.get(key) or {}).get(sub)).__name__}")
    if data.get("health", {}).get("level") not in ("green", "amber", "red"):
        bad.append(f"health.level: {data.get('health')}")
    if bad:
        print("SNAPSHOT CONTRACT BROKEN:\n  " + "\n  ".join(bad))
        return 1
    print(f"snapshot contract: {len(EXPECTED)} keys, health {data['health']['level']}")
    return 0


if __name__ == "__main__":
    import psycopg2.extras  # noqa: F401  (Json adapter)
    sys.exit(main())
