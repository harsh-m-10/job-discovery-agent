# Restoring a backup

Backups are made nightly by `.github/workflows/backup.yml`: a pg_dump of the
`public` schema, proven restorable before it is kept, encrypted with
[age](https://age-encryption.org) to the public key in
`config/backup_recipient.txt`, and pushed to the private repository
`harsh-m-10/job-agent-backups`. The newest 30 are kept.

You need: the age **private** key (kept offline, never in any repo), Postgres
17 client tools (`pg_restore`), Python with `psycopg2`, and `age`.

## 1. Fetch and decrypt

```sh
git clone --depth 1 git@github.com:harsh-m-10/job-agent-backups.git
cd job-agent-backups
ls job-agent-*.dump.age | tail -3          # newest last
NAME=job-agent-2026-10-16T2041Z            # pick one
age -d -i /path/to/backup.key -o "$NAME.dump" "$NAME.dump.age"
```

Each dump has a `$NAME.counts.json` beside it: the row count of every table
just before and just after the dump.

## 2. Restore and verify in one step

Into **an empty database** — a new Supabase project, or a local scratch one:

```sh
python scripts/backup.py restore \
  --dump "$NAME.dump" --counts "$NAME.counts.json" \
  --target "postgresql://USER:PASSWORD@HOST:5432/postgres"
```

It restores everything in the `public` schema except the `CREATE SCHEMA public`
statement (every fresh database already has that schema; the first restore
drill failed on exactly this), then checks every table came back with the row
count recorded at dump time. It exits non-zero on any mismatch.

For a new Supabase project use its session-pooler connection string (port
5432), as the direct host is IPv6-only.

## 3. Point the system at it

If this is a replacement project, update `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY` (and `SUPABASE_DB_PASSWORD`) in: the GitHub Actions
secrets, the Vercel project environment, and your local `.env`. Then
`gh workflow run ingest.yml -f source=manual` and watch it finish.

## Rehearsing without production data

The CI job `backup-selftest` runs this whole cycle on every push against
throwaway databases. To rehearse locally against a real dump, see the drill
recorded in docs/DECISIONS.md (Phase 0.1): initdb a scratch cluster, restore
into it, delete it afterwards — the dump holds personal data.
