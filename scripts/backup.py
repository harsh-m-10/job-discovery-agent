#!/usr/bin/env python
"""Dump the database and prove the dump restores (Phase 0.1).

    python scripts/backup.py dump   --out job-agent.dump --counts counts.json
    python scripts/backup.py verify --dump job-agent.dump --counts counts.json \\
                                    --target postgresql://postgres@localhost:5432/scratch

Supabase's free tier keeps no backups. Everything this project has learned --
4,400 postings with their first-seen times, every score, the funnel, 1,118
imported contacts -- lives in one 41 MB database with no copy anywhere.

`dump` writes a pg_dump custom-format archive of the `public` schema (the only
schema this project uses) and records each table's row count immediately
before and immediately after the dump. `verify` restores the archive into an
empty database and checks every table landed with a count between those two.
A backup that has never been restored is a hope, not a backup, so
.github/workflows/backup.yml runs verify on every dump before encrypting it.

Why a range rather than one number: ingest runs every 15 minutes and is not
paused for the backup. pg_dump reads one consistent snapshot, but the counts
are separate queries, so a table may grow or shrink by a few rows between
them. Anything outside [min(before, after), max(before, after)] is a real
discrepancy.

Connection: the ap-south-1 session pooler, because db.<ref>.supabase.co is
IPv6-only. Needs SUPABASE_URL and SUPABASE_DB_PASSWORD; pg_dump/pg_restore
must be major version 17 to match the server.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse as up
from pathlib import Path

import psycopg2

DEFAULT_POOLER = "aws-0-ap-south-1.pooler.supabase.com"
TABLES = ("companies", "jobs", "run_log", "job_scores", "connections",
          "applications", "notifications")


def source_params() -> dict[str, str]:
    # BACKUP_SOURCE_URL overrides everything. CI uses it to run the whole
    # dump -> restore -> verify cycle against a throwaway service database, so
    # the mechanics are tested on every push without any production secret.
    override = os.environ.get("BACKUP_SOURCE_URL", "")
    if override:
        u = up.urlparse(override)
        return {"host": u.hostname or "localhost", "port": str(u.port or 5432),
                "user": u.username or "postgres",
                "dbname": (u.path or "/postgres").lstrip("/"),
                "password": u.password or ""}
    url = os.environ.get("SUPABASE_URL", "")
    password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not url or not password:
        sys.exit("SUPABASE_URL and SUPABASE_DB_PASSWORD must be set")
    ref = up.urlparse(url).hostname.split(".")[0]
    return {"host": os.environ.get("SUPABASE_POOLER_HOST", DEFAULT_POOLER),
            "port": "5432", "user": f"postgres.{ref}", "dbname": "postgres",
            "password": password}


def count_rows(conn) -> dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f'select count(*) from public."{table}"')
            out[table] = cur.fetchone()[0]
    return out


def cmd_dump(args) -> int:
    params = source_params()
    conn = psycopg2.connect(connect_timeout=30, **params)
    before = count_rows(conn)

    env = {**os.environ, "PGPASSWORD": params["password"]}
    subprocess.run(
        ["pg_dump", "--format=custom", "--compress=9", "--schema=public",
         "--no-owner", "--no-privileges",
         f"--host={params['host']}", f"--port={params['port']}",
         f"--username={params['user']}", f"--dbname={params['dbname']}",
         f"--file={args.out}"],
        env=env, check=True)

    after = count_rows(conn)
    conn.close()
    Path(args.counts).write_text(json.dumps(
        {t: [before[t], after[t]] for t in TABLES}, indent=2), encoding="utf-8")
    size_kb = Path(args.out).stat().st_size // 1024
    print(f"dumped public schema -> {args.out} ({size_kb} KB)")
    for t in TABLES:
        print(f"  {t:<14} {before[t]:>6} -> {after[t]:>6}")
    return 0


def compare(expected: dict[str, list[int]], restored: dict[str, int]) -> list[str]:
    """Pure: every table present, every count inside its [before, after] range."""
    problems = []
    for table, pair in expected.items():
        lo, hi = min(pair), max(pair)
        got = restored.get(table)
        if got is None:
            problems.append(f"{table}: missing from the restore")
        elif not lo <= got <= hi:
            problems.append(f"{table}: restored {got}, expected {lo}..{hi}")
    return problems


def restore_list(dump: str, env: dict) -> str:
    """Write the archive's table of contents minus `CREATE SCHEMA public`.

    Found by the first restore drill, 2026-10-15: dumping a single schema on
    PG 17 records the schema's own creation, and every fresh database --
    vanilla Postgres or a new Supabase project alike -- already has `public`,
    so the restore died on its first statement. Skipping that one TOC entry is
    narrower than --clean, which would DROP SCHEMA public on the target first.
    Returns the path of the filtered list for pg_restore --use-list.
    """
    listing = subprocess.run(["pg_restore", "--list", dump], env=env,
                             capture_output=True, text=True, check=True).stdout
    kept = [line for line in listing.splitlines()
            if not (" SCHEMA - public " in line or line.rstrip().endswith("SCHEMA - public"))]
    path = f"{dump}.toc"
    Path(path).write_text("\n".join(kept) + "\n", encoding="utf-8")
    return path


def cmd_verify(args) -> int:
    expected = json.loads(Path(args.counts).read_text(encoding="utf-8"))
    target = up.urlparse(args.target)
    env = {**os.environ}
    if target.password:
        env["PGPASSWORD"] = target.password
    toc = restore_list(args.dump, env)
    result = subprocess.run(
        ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
         f"--use-list={toc}", f"--dbname={args.target}", args.dump],
        env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print("pg_restore failed:\n" + result.stderr[-2000:], file=sys.stderr)
        return 1

    conn = psycopg2.connect(args.target, connect_timeout=30)
    try:
        restored = count_rows(conn)
    except psycopg2.Error as exc:
        print(f"restored database is missing a table: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    problems = compare(expected, restored)
    for t in TABLES:
        lo, hi = min(expected[t]), max(expected[t])
        flag = "ok " if lo <= restored.get(t, -1) <= hi else "BAD"
        print(f"  {flag} {t:<14} restored {restored.get(t, '-'):>6}  expected {lo}..{hi}")
    if problems:
        print("RESTORE DOES NOT MATCH:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print("restore verified: every table present with the expected row count")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--out", required=True)
    d.add_argument("--counts", required=True)
    # `restore` is the same operation under the name you would look for in an
    # emergency: it restores into the target and then proves the counts match.
    v = sub.add_parser("verify", aliases=["restore"])
    v.add_argument("--dump", required=True)
    v.add_argument("--counts", required=True)
    v.add_argument("--target", required=True, help="URL of an EMPTY database")
    args = ap.parse_args()
    return cmd_dump(args) if args.cmd == "dump" else cmd_verify(args)


# Note for readers of a restored database: run_log ids are not contiguous
# (rows are only ever appended, but ids are consumed by aborted inserts), so
# compare counts, never max(id).


if __name__ == "__main__":
    sys.exit(main())
