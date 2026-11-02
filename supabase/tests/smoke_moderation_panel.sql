-- Smoke test for the moderation panel (migration 26) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

-- A city admin of Ambato, a catalog editor, a stakeholder agent (no moderation), a validated case
-- of three reports from three devices, one of them flagged as a reused photo.
insert into auth.users (id, email) values
  ('e6000000-0000-4000-8000-000000000001', 'mod@test'), ('e6000000-0000-4000-8000-000000000002', 'cat@test'),
  ('e6000000-0000-4000-8000-000000000003', 'agent@test');
insert into profiles (user_id, display_name) values
  ('e6000000-0000-4000-8000-000000000001', 'Mod'), ('e6000000-0000-4000-8000-000000000002', 'Cat'),
  ('e6000000-0000-4000-8000-000000000003', 'Agent');
insert into user_roles (user_id, role_id, city_id, stakeholder_id) values
  ('e6000000-0000-4000-8000-000000000001', 'city_admin', 'ambato', null),
  ('e6000000-0000-4000-8000-000000000002', 'catalog_editor', null, null),
  ('e6000000-0000-4000-8000-000000000003', 'stakeholder_agent', null, '00000000-0000-4000-8000-000000000001');
insert into devices (id, trust) values
  ('a6000000-0000-4000-8000-000000000001', 0.5), ('a6000000-0000-4000-8000-000000000002', 0.5),
  ('a6000000-0000-4000-8000-000000000003', 0.5);
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices, weighted_support)
values ('b6000000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole',
        st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, 'validated', 3, 3, 1.5);
insert into reports (id, device_id, city_id, geom, ip_prefix, status, domain_id, category_id, catalog_version,
                     severity, cluster_id, raw_text, raw_text_redacted, consistency_score, spec)
select ('d6000000-0000-4000-8000-00000000000' || i)::uuid, ('a6000000-0000-4000-8000-00000000000' || i)::uuid, 'ambato',
       st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, '190.1.' || i || '.0/24', 'clustered', 'roads', 'pothole', 1,
       3, 'b6000000-0000-4000-8000-000000000001', 'Bache, llamar al 0999999999', 'Bache, llamar al [phone]', 1.0,
       '{"summary_en": "Pothole"}'
  from generate_series(1, 3) i;
insert into moderation_flags (city_id, report_id, reason, source, evidence) values
  ('ambato', 'd6000000-0000-4000-8000-000000000003', 'reused_photo', 'orchestrator', '{"matches": []}');

do $$
declare x jsonb;
begin
  -- Nobody signed in, or an agent: no access.
  perform set_config('request.jwt.claims', '{"sub": "e6000000-0000-4000-8000-000000000003"}', true);
  assert admin_access() = '{"city_id": "ambato", "moderate": false, "catalog": false, "audit": false}'::jsonb, admin_access()::text;
  begin perform moderation_queue(); assert false; exception when sqlstate 'PT403' then null; end;
  begin perform moderate_report('d6000000-0000-4000-8000-000000000003', 'hide', 'spam'); assert false;
  exception when sqlstate 'PT403' then null; end;

  -- The city admin sees the flagged report, redacted text only.
  perform set_config('request.jwt.claims', '{"sub": "e6000000-0000-4000-8000-000000000001"}', true);
  x := admin_access();
  assert x ->> 'moderate' = 'true' and x ->> 'catalog' = 'false' and x ->> 'audit' = 'true', x::text;
  assert admin_access('quito') ->> 'moderate' = 'false', 'a city role is for its city';
  x := moderation_queue();
  assert jsonb_array_length(x) = 1 and x -> 0 ->> 'short_ref' = 'r_d6000000' and x -> 0 -> 'flags' -> 0 ->> 'reason' = 'reused_photo', x::text;
  assert x -> 0 ->> 'raw_text_redacted' = 'Bache, llamar al [phone]' and not (x -> 0 ? 'raw_text'), x::text;
  assert jsonb_array_length(moderation_queue('ambato', 'recent')) = 3;
  assert jsonb_array_length(moderation_queue('ambato', 'hidden')) = 0;

  -- Hiding needs a reason; once hidden, the flag is closed and the case recounts without it.
  begin perform moderate_report('d6000000-0000-4000-8000-000000000003', 'hide'); assert false;
  exception when others then assert sqlerrm = 'reason_required', sqlerrm; end;
  x := moderate_report('d6000000-0000-4000-8000-000000000003', 'hide', 'reused_photo', 'same photo as r_xxx');
  assert x ->> 'flags_closed' = '1' and x ->> 'cluster_reports' = '2' and x ->> 'cluster_status' = 'validated', x::text;
  assert (select moderation_status = 'hidden' and moderated_by is not null from reports where id = 'd6000000-0000-4000-8000-000000000003');
  assert (select resolution from moderation_flags where report_id = 'd6000000-0000-4000-8000-000000000003') = 'hidden';
  assert jsonb_array_length(moderation_queue()) = 0 and jsonb_array_length(moderation_queue('ambato', 'hidden')) = 1;
  begin perform moderate_report('d6000000-0000-4000-8000-000000000003', 'hide', 'spam'); assert false;
  exception when sqlstate 'PT409' then null; end;

  -- Hiding every report hides the case from the public map; restoring one shows it again.
  perform moderate_report('d6000000-0000-4000-8000-000000000001', 'hide', 'spam');
  x := moderate_report('d6000000-0000-4000-8000-000000000002', 'hide', 'spam');
  assert x ->> 'cluster_visible' = 'false' and x ->> 'cluster_status' = 'validated', 'validated never regresses: ' || x::text;
  assert not exists (select 1 from public_clusters where id = 'b6000000-0000-4000-8000-000000000001');
  x := moderate_report('d6000000-0000-4000-8000-000000000002', 'restore');
  assert x ->> 'cluster_visible' = 'true' and x ->> 'cluster_reports' = '1', x::text;
  assert (select count(*) from audit_log where action like 'report.%' and entity_id like 'd6000000%') = 4;
  assert jsonb_array_length(admin_history()) = 4 and admin_history() -> 0 ->> 'actor' = 'Mod';

  -- Keep: a false alarm closes the flag and changes nothing else.
  insert into moderation_flags (city_id, report_id, reason, source) values
    ('ambato', 'd6000000-0000-4000-8000-000000000002', 'impossible_gps', 'trigger');
  x := moderate_report('d6000000-0000-4000-8000-000000000002', 'keep');
  assert x ->> 'flags_closed' = '1' and (select moderation_status from reports where id = 'd6000000-0000-4000-8000-000000000002') = 'visible';

  -- The city admin may read the catalog but not change it.
  assert (select count(*) from jsonb_array_elements(admin_catalog()) e where e ->> 'id' = 'pothole' and (e ->> 'reports_30d')::int = 3) = 1;
  begin perform set_category_status('pothole', 'retired'); assert false; exception when sqlstate 'PT403' then null; end;

  -- The catalog editor retires and brings back a category; "other" stays.
  perform set_config('request.jwt.claims', '{"sub": "e6000000-0000-4000-8000-000000000002"}', true);
  assert admin_access() ->> 'catalog' = 'true' and admin_access() ->> 'moderate' = 'false';
  x := set_category_status('pothole', 'retired', 'testing');
  assert x ->> 'to' = 'retired' and (select status from categories where id = 'pothole') = 'retired', x::text;
  begin perform set_category_status('pothole', 'retired'); assert false; exception when sqlstate 'PT409' then null; end;
  begin perform set_category_status('other', 'retired'); assert false;
  exception when others then assert sqlerrm = 'category_required', sqlerrm; end;
  perform set_category_status('pothole', 'active');
  assert (select count(*) from audit_log where action in ('category.retire', 'category.activate') and entity_id = 'pothole') = 2;
  begin perform moderation_queue(); assert false; exception when sqlstate 'PT403' then null; end;
end $$;
rollback;
\echo 'smoke_moderation_panel: ok'
