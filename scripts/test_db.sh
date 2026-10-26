#!/usr/bin/env bash
# Apply shim + migrations + seeds to an empty Postgres 16 with PostGIS and run SQL tests.
# Usage: DATABASE_URL=postgres://postgres@localhost:5432/cp_test scripts/test_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?set DATABASE_URL to an empty database}"
run() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$1"; }
run supabase/tests/local_shim.sql
for f in supabase/migrations/*.sql; do run "$f"; done
run supabase/seed/catalog.sql
run supabase/seed/ambato.sql
for f in supabase/tests/smoke_*.sql; do run "$f"; done
