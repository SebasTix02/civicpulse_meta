-- CivicPulse: extensions used by the schema.
-- Supabase enables pgcrypto and uuid helpers by default; PostGIS and pg_cron are opt-in.

create extension if not exists postgis with schema extensions;
create extension if not exists pgcrypto with schema extensions;
-- pg_cron and pg_net are enabled from the Supabase dashboard (Database > Extensions)
-- because they live in their own schemas; see supabase/README.md.
