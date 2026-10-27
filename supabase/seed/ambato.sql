-- PostGIS names are schema-qualified: the Supabase CLI sends seeds in a batch where a
-- `set search_path` does not carry over.
set search_path = public, extensions;

-- City and stakeholders for the hackathon demo. The boundary is an APPROXIMATE bounding box
-- of the Ambato canton; replace it with the official GIS boundary (day 1 task).
insert into cities (id, name, boundary, default_lang, timezone, is_enabled, sandbox_enabled)
values ('ambato', 'Ambato',
        extensions.st_multi(extensions.st_makeenvelope(-78.80, -1.42, -78.45, -1.08, 4326))::extensions.geography,
        'es', 'America/Guayaquil', true, true)
on conflict (id) do update set boundary = excluded.boundary;

-- Real stakeholders are created unverified; nobody receives dispatches until an admin
-- verifies them and invites members.
insert into stakeholders (id, city_id, name, kind, brief_profile, brief_lang, verified) values
  ('00000000-0000-4000-8000-000000000001', 'ambato', 'RRT - Road Repair Test Co.', 'government', 'operational', 'es', false),
  ('00000000-0000-4000-8000-000000000002', 'ambato', 'WST - Water Services Test', 'utility', 'operational', 'es', false)
on conflict (id) do nothing;

insert into stakeholder_coverage (stakeholder_id, domain_id) values
  ('00000000-0000-4000-8000-000000000001', 'roads'),
  ('00000000-0000-4000-8000-000000000001', 'general'),
  ('00000000-0000-4000-8000-000000000001', 'transit'),
  ('00000000-0000-4000-8000-000000000002', 'water')
on conflict do nothing;
