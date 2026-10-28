-- Minimal stand-in for the Supabase platform so migrations can be tested on plain Postgres
-- (CI and local without Docker). Never applied to a real Supabase project.
create schema if not exists auth;
create schema if not exists extensions;
-- The postgis/postgis image (CI) pre-installs PostGIS in public; Supabase keeps it in extensions,
-- and the seed calls extensions.st_*. PostGIS cannot be moved, so reinstall it there.
do $$ begin
  if exists (select 1 from pg_extension e join pg_namespace n on n.oid = e.extnamespace
              where e.extname = 'postgis' and n.nspname <> 'extensions') then
    drop extension if exists postgis_tiger_geocoder, postgis_topology, postgis cascade;
  end if;
end $$;
create table if not exists auth.users (id uuid primary key, email text);
create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;
do $$ begin
  create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
  create role supabase_auth_admin nologin;
exception when duplicate_object then null; end $$;
grant usage on schema public, auth, extensions to anon, authenticated, service_role;
-- Supabase grants table access to these roles by default and relies on RLS.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
