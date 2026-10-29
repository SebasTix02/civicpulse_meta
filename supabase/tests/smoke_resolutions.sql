-- Smoke test for the stakeholder closure (migration 15) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

-- An agent (may submit) and an admin (may approve) of the roads stakeholder, one accepted dispatch.
insert into auth.users (id, email) values
  ('e1000000-0000-4000-8000-000000000001', 'agent@test'), ('e1000000-0000-4000-8000-000000000002', 'admin@test');
insert into profiles (user_id, display_name) values
  ('e1000000-0000-4000-8000-000000000001', 'Agent'), ('e1000000-0000-4000-8000-000000000002', 'Admin');
insert into user_roles (user_id, role_id, stakeholder_id) values
  ('e1000000-0000-4000-8000-000000000001', 'stakeholder_agent', '00000000-0000-4000-8000-000000000001'),
  ('e1000000-0000-4000-8000-000000000002', 'stakeholder_admin', '00000000-0000-4000-8000-000000000001');
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices)
values ('b2000000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole',
        st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, 'validated', 1, 1);
insert into devices (id, trust) values ('a2000000-0000-4000-8000-000000000001', 0.5);
insert into reports (id, device_id, city_id, geom, gps_geom, gps_accuracy_m, ip_prefix, status, domain_id, category_id,
                     catalog_version, severity, cluster_id, raw_text)
values ('d2000000-0000-4000-8000-000000000001', 'a2000000-0000-4000-8000-000000000001', 'ambato',
        st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography,
        8, '190.1.1.0/24', 'clustered', 'roads', 'pothole', 1, 3, 'b2000000-0000-4000-8000-000000000001', 'Bache');
insert into dispatches (cluster_id, stakeholder_id, brief) values
  ('b2000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', '{"title": "Bache"}');

do $$
declare
  cl  constant uuid := 'b2000000-0000-4000-8000-000000000001';
  sh  constant uuid := '00000000-0000-4000-8000-000000000001';
  x   jsonb;
  n   jsonb;
  res uuid;
begin
  perform set_config('request.jwt.claims', '{"sub": "e1000000-0000-4000-8000-000000000001"}', true);
  begin
    perform issue_resolution_nonce(cl, sh);
    assert false, 'no photo before the dispatch is accepted';
  exception when sqlstate 'PT409' then null;
  end;
  perform set_dispatch_status(cl, sh, 'accepted');

  n := issue_resolution_nonce(cl, sh);
  assert n ->> 'bucket' = 'raw' and n ->> 'upload_path' like 'resolutions/e1000000-%', n::text;
  assert can_upload_capture('raw', n ->> 'upload_path'), 'the actor may upload to that exact path';
  assert not can_upload_capture('raw', 'resolutions/someone-else/x.jpg');
  begin
    perform submit_stakeholder_resolution(cl, sh, n ->> 'nonce', 'resolutions/x/y.jpg');
    assert false, 'path must match the nonce';
  exception when sqlstate 'PT422' then null;
  end;
  x := submit_stakeholder_resolution(cl, sh, n ->> 'nonce', n ->> 'upload_path', 1280, 960);
  res := (x ->> 'resolution_id')::uuid;
  assert x ->> 'cluster_status' = 'resolution_pending', x::text;
  assert (select status from clusters where id = cl) = 'resolution_pending';
  assert (select storage_path from photos p join resolutions r on r.photo_id = p.id where r.id = res)
         = 'raw/' || (n ->> 'upload_path');
  assert (select latest_resolution ->> 'ai_status' from panel_inbox) = 'pending';
  begin
    perform issue_resolution_nonce(cl, sh);
    assert false, 'one open resolution per cluster';
  exception when sqlstate 'PT409' then null;
  end;
  begin
    perform review_resolution(res, 'approve', 'looks fixed');
    assert false, 'an agent cannot approve';
  exception when sqlstate 'PT403' then null;
  end;

  -- The admin: no approval before the AI answers; against the AI only with a note; reject reopens.
  perform set_config('request.jwt.claims', '{"sub": "e1000000-0000-4000-8000-000000000002"}', true);
  assert (select (latest_resolution ->> 'can_review')::boolean from panel_inbox);
  begin
    perform review_resolution(res, 'approve', 'looks fixed');
    assert false, 'the AI result comes first';
  exception when sqlstate 'PT409' then null;
  end;
  update resolutions set ai_status = 'PERSISTENT_ISSUE' where id = res;
  begin
    perform review_resolution(res, 'approve');
    assert false, 'approving against the AI needs a note';
  exception when sqlstate 'PT422' then null;
  end;
  x := review_resolution(res, 'reject', 'the photo shows another street');
  assert x ->> 'cluster_status' = 'acknowledged', x::text;
  assert (select latest_resolution ->> 'outcome' from panel_inbox) = 'rejected';

  -- A second photo; the orchestrator marks it a candidate; the admin approves it.
  n := issue_resolution_nonce(cl, sh);
  res := (submit_stakeholder_resolution(cl, sh, n ->> 'nonce', n ->> 'upload_path',
          p_gps_location => '{"lat": -1.24005, "lon": -78.62}', p_gps_accuracy_m => 6) ->> 'resolution_id')::uuid;
  x := closure_location(res, 'd2000000-0000-4000-8000-000000000001');
  assert (x ->> 'distance_m')::float between 5 and 6 and (x ->> 'after_accuracy_m')::float = 6
         and (x ->> 'before_accuracy_m')::float = 8, x::text;
  update resolutions set ai_status = 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW' where id = res;
  x := review_resolution(res, 'approve');
  assert x ->> 'outcome' = 'resolved' and not (x ->> 'ai_overridden')::boolean, x::text;
  assert (select status from clusters where id = cl) = 'resolved';
  assert (select resolved_at from clusters where id = cl) is not null;
  assert (select status from dispatches where cluster_id = cl) = 'closed';
  assert (select count(*) from resolution_reviews) = 2;
  assert (select count(*) from audit_log where action like 'resolution.%') = 4, 'submit x2, reject, approve';
  begin
    perform review_resolution(res, 'reject');
    assert false, 'decided once';
  exception when sqlstate 'PT409' then null;
  end;
end $$;
rollback;
\echo 'smoke_resolutions: ok'
