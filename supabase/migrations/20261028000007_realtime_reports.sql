-- Live status for the citizen who sent a report (UX flows: 8-step timeline).
-- Realtime only delivers rows the subscriber can select, so own_reports (device_id = auth.uid())
-- keeps each device to its own reports. Skipped where the publication does not exist (plain
-- Postgres in CI).
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
     and not exists (select 1 from pg_publication_tables
                      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'reports') then
    alter publication supabase_realtime add table public.reports;
  end if;
end $$;
