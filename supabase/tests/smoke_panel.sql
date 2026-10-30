-- Smoke test for the stakeholder panel (migration 14) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

-- An agent of the roads stakeholder, an outsider with no role, and one dispatch with a brief.
insert into auth.users (id, email) values
  ('e0000000-0000-4000-8000-000000000001', 'agent@test'), ('e0000000-0000-4000-8000-000000000002', 'nobody@test');
insert into profiles (user_id, display_name) values
  ('e0000000-0000-4000-8000-000000000001', 'Agent'), ('e0000000-0000-4000-8000-000000000002', 'Nobody');
insert into user_roles (user_id, role_id, stakeholder_id) values
  ('e0000000-0000-4000-8000-000000000001', 'stakeholder_agent', '00000000-0000-4000-8000-000000000001');
insert into devices (id, trust) values ('a1000000-0000-4000-8000-000000000001', 0.5);
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices)
values ('b1000000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole',
        st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, 'validated', 1, 1);
insert into reports (id, device_id, city_id, geom, ip_prefix, status, domain_id, category_id, catalog_version,
                     severity, cluster_id, raw_text, raw_text_redacted, spec)
values ('d1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'ambato',
        st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, '190.1.1.0/24', 'clustered', 'roads', 'pothole', 1,
        3, 'b1000000-0000-4000-8000-000000000001', 'Bache, llamar al 0999999999', 'Bache, llamar al [phone]',
        '{"summary_en": "Pothole on the street"}');
insert into dispatches (cluster_id, stakeholder_id, brief) values
  ('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', '{"title": "Bache"}'),
  ('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002', null);

do $$
declare x jsonb;
begin
  -- The agent sees the dispatch that has a brief, and the report without device or raw text.
  perform set_config('request.jwt.claims', '{"sub": "e0000000-0000-4000-8000-000000000001"}', true);
  assert (select count(*) from panel_inbox) = 1, 'only the dispatch with a brief, of my stakeholder';
  assert (select cluster ->> 'category_id' from panel_inbox) = 'pothole';
  assert (select short_ref from panel_cluster_reports) = 'r_d1000000';
  assert (select raw_text_redacted from panel_cluster_reports) = 'Bache, llamar al [phone]';
  assert (select count(*) from my_stakeholders) = 1 and (select can_respond from my_stakeholders);

  x := set_dispatch_status('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'seen');
  assert x ->> 'status' = 'seen', x::text;
  begin
    perform set_dispatch_status('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'declined');
    assert false, 'declined without a note must fail';
  exception when sqlstate 'PT422' then null;
  end;
  x := set_dispatch_status('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'accepted');
  assert x ->> 'cluster_status' = 'acknowledged', x::text;
  assert (select count(*) from audit_log where action like 'dispatch.%') = 2, 'seen + accepted audited';
  begin
    perform set_dispatch_status('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'declined', 'x');
    assert false, 'accepted cannot go back to declined';
  exception when sqlstate 'PT409' then null;
  end;

  -- Someone without a role, or an anonymous citizen, sees nothing and cannot act.
  perform set_config('request.jwt.claims', '{"sub": "e0000000-0000-4000-8000-000000000002"}', true);
  assert (select count(*) from panel_inbox) = 0 and (select count(*) from panel_cluster_reports) = 0;
  begin
    perform set_dispatch_status('b1000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'seen');
    assert false, 'an outsider cannot touch the dispatch';
  exception when sqlstate 'PT404' then null;
  end;
  perform set_config('request.jwt.claims',
                     '{"sub": "e0000000-0000-4000-8000-000000000001", "is_anonymous": true}', true);
  assert (select count(*) from panel_inbox) = 0, 'anonymous sessions never see the panel';

  -- The public map row of the same case (migration 18): category name, no photo without a confirmed one.
  assert (select category_name ->> 'es' from public_clusters where id = 'b1000000-0000-4000-8000-000000000001') is not null;
  assert (select photo_public_path from public_clusters where id = 'b1000000-0000-4000-8000-000000000001') is null;
  -- Its gallery (migration 19) only ever lists public copies.
  assert not exists (select 1 from public_case_photos('b1000000-0000-4000-8000-000000000001')
                      where photo_public_path not like 'public/%'), 'only anonymized public copies';
end $$;
rollback;
\echo 'smoke_panel: ok'
