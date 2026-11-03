-- Smoke test for the public community closure (migration 30) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

-- A validated pothole with one report (photo confirmed); the sender A, two reviewers B and C with a
-- report of their own elsewhere, and D who never reported.
insert into devices (id, trust) values
  ('a9000000-0000-4000-8000-00000000000a', 0.5), ('a9000000-0000-4000-8000-00000000000b', 0.6),
  ('a9000000-0000-4000-8000-00000000000c', 0.5), ('a9000000-0000-4000-8000-00000000000d', 0.5),
  ('a9000000-0000-4000-8000-00000000000e', 0.5);
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices) values
  ('b9100000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole', st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, 'validated', 1, 1),
  ('b9100000-0000-4000-8000-000000000002', 'ambato', 'roads', 'pothole', st_setsrid(st_makepoint(-78.60, -1.22), 4326)::geography, 'candidate', 2, 2);
insert into capture_nonces (nonce, device_id, purpose, expires_at, used_at) values
  ('n-before', 'a9000000-0000-4000-8000-00000000000e', 'report', now() + interval '5 minutes', now());
insert into photos (id, storage_path, public_path) values
  ('f9000000-0000-4000-8000-000000000001', 'raw/e/n-before.jpg', 'public/reports/r/v1.jpg');
insert into reports (id, device_id, city_id, geom, status, domain_id, category_id, catalog_version, severity, cluster_id,
                     intake_status, current_photo_version) values
  ('d9000000-0000-4000-8000-000000000001', 'a9000000-0000-4000-8000-00000000000e', 'ambato',
   st_setsrid(st_makepoint(-78.62, -1.24), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
   'b9100000-0000-4000-8000-000000000001', 'REPORT_CONFIRMED_BY_PHOTO', null),
  ('d9000000-0000-4000-8000-000000000002', 'a9000000-0000-4000-8000-00000000000b', 'ambato',
   st_setsrid(st_makepoint(-78.60, -1.22), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
   'b9100000-0000-4000-8000-000000000002', null, null),
  ('d9000000-0000-4000-8000-000000000003', 'a9000000-0000-4000-8000-00000000000c', 'ambato',
   st_setsrid(st_makepoint(-78.60, -1.22), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
   'b9100000-0000-4000-8000-000000000002', null, null);
insert into report_photos (report_id, version, photo_id, capture_nonce) values
  ('d9000000-0000-4000-8000-000000000001', 1, 'f9000000-0000-4000-8000-000000000001', 'n-before');
update reports set current_photo_version = 1 where id = 'd9000000-0000-4000-8000-000000000001';

create function pg_temp.as_device(p text) returns void language sql as $$
  select set_config('request.jwt.claims', json_build_object('sub', p, 'is_anonymous', true)::text, true)
$$;

do $$
declare
  cl  constant uuid := 'b9100000-0000-4000-8000-000000000001';
  a   constant text := 'a9000000-0000-4000-8000-00000000000a';
  near constant jsonb := '{"lat": -1.2405, "lon": -78.6200}';  -- about 55 m away
  n   jsonb;
  x   jsonb;
  res uuid;
begin
  perform pg_temp.as_device(a);
  n := issue_capture_nonce('community_resolution');
  assert can_upload_capture('raw', n ->> 'upload_path');
  begin perform submit_community_resolution(cl, n ->> 'nonce', n ->> 'upload_path', null, null); assert false;
  exception when others then assert sqlerrm = 'gps_required', sqlerrm; end;
  begin perform submit_community_resolution(cl, n ->> 'nonce', n ->> 'upload_path', '{"lat": -1.25, "lon": -78.62}', 10); assert false;
  exception when others then assert sqlerrm = 'too_far_from_case', sqlerrm; end;
  begin perform submit_community_resolution(cl, n ->> 'nonce', n ->> 'upload_path', near, 80); assert false;
  exception when others then assert sqlerrm = 'gps_too_inaccurate', sqlerrm; end;
  begin perform submit_community_resolution('b9100000-0000-4000-8000-000000000002', n ->> 'nonce', n ->> 'upload_path', near, 10); assert false;
  exception when others then assert sqlerrm = 'cluster_not_awaiting_fix', sqlerrm; end;

  x := submit_community_resolution(cl, n ->> 'nonce', n ->> 'upload_path', near, 12, 1280, 960);
  res := (x ->> 'resolution_id')::uuid;
  assert (select status from clusters where id = cl) = 'resolution_pending';
  assert (select before_report_id from resolutions where id = res) = 'd9000000-0000-4000-8000-000000000001';
  assert (select prior_cluster_status from resolutions where id = res) = 'validated';
  assert (select gps_accuracy_m from resolutions where id = res) = 12;
  assert (select storage_path from photos p join resolutions r on r.photo_id = p.id where r.id = res) = 'raw/' || (n ->> 'upload_path');
  assert (select ai_status from my_community_resolutions where id = res) = 'pending';

  -- B before the AI answers: nothing to review yet, and an early vote is refused.
  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000b');
  assert jsonb_array_length(nearby_resolutions_to_review(-1.24, -78.62) -> 'items') = 0;
  begin perform review_community_resolution(res, 'approve'); assert false;
  exception when others then assert sqlerrm = 'ai_pending', sqlerrm; end;

  -- The closure stage answers (service role) and writes the AFTER public copy.
  update resolutions set ai_status = 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW' where id = res;
  update photos set public_path = 'public/resolutions/x/after.jpg' where id = (select photo_id from resolutions where id = res);

  x := nearby_resolutions_to_review(-1.24, -78.62);
  assert (x ->> 'can_review')::boolean and jsonb_array_length(x -> 'items') = 1, x::text;
  assert x -> 'items' -> 0 ->> 'before_public_path' = 'public/reports/r/v1.jpg', x::text;
  assert jsonb_array_length(nearby_resolutions_to_review(-1.30, -78.62) -> 'items') = 0, 'too far to review';

  perform pg_temp.as_device(a);
  assert jsonb_array_length(nearby_resolutions_to_review(-1.24, -78.62) -> 'items') = 0, 'own photo hidden';
  begin perform review_community_resolution(res, 'approve'); assert false;
  exception when others then assert sqlerrm = 'self_review', sqlerrm; end;
  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000d');
  begin perform review_community_resolution(res, 'approve'); assert false;
  exception when others then assert sqlerrm = 'trust_too_low', sqlerrm; end;

  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000b');
  x := review_community_resolution(res, 'approve');
  assert x ->> 'outcome' = 'open' and (x ->> 'approvals')::int = 1, x::text;
  begin perform review_community_resolution(res, 'approve'); assert false;
  exception when others then assert sqlerrm = 'already_reviewed', sqlerrm; end;
  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000c');
  x := review_community_resolution(res, 'approve');
  assert x ->> 'outcome' = 'resolved' and x ->> 'cluster_status' = 'resolved', x::text;
  assert (select status from clusters where id = cl) = 'resolved' and (select resolved_at from clusters where id = cl) is not null;
end $$;

-- A second case: the AI could not compare; after 7 days the closure is rejected and the case reopens.
do $$
declare
  cl uuid := 'b9100000-0000-4000-8000-000000000002';
  n jsonb;
  res uuid;
begin
  update clusters set status = 'acknowledged' where id = cl;
  insert into capture_nonces (nonce, device_id, purpose, expires_at, used_at) values
    ('n-before-2', 'a9000000-0000-4000-8000-00000000000b', 'report', now() + interval '5 minutes', now());
  insert into photos (id, storage_path, public_path) values ('f9000000-0000-4000-8000-000000000002', 'raw/b/n2.jpg', 'public/reports/r2/v1.jpg');
  insert into report_photos (report_id, version, photo_id, capture_nonce) values
    ('d9000000-0000-4000-8000-000000000002', 1, 'f9000000-0000-4000-8000-000000000002', 'n-before-2');
  update reports set current_photo_version = 1 where id = 'd9000000-0000-4000-8000-000000000002';
  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000a');
  n := issue_capture_nonce('community_resolution');
  res := (submit_community_resolution(cl, n ->> 'nonce', n ->> 'upload_path', '{"lat": -1.22, "lon": -78.60}', 8) ->> 'resolution_id')::uuid;
  update resolutions set ai_status = 'NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE' where id = res;
  perform pg_temp.as_device('a9000000-0000-4000-8000-00000000000c');
  begin perform review_community_resolution(res, 'approve'); assert false;
  exception when others then assert sqlerrm = 'ai_not_candidate', sqlerrm; end;
  assert expire_community_resolutions() = 0, 'not old yet';
  update resolutions set created_at = now() - interval '8 days' where id = res;
  assert expire_community_resolutions() = 1;
  assert (select outcome from resolutions where id = res) = 'rejected';
  assert (select status from clusters where id = cl) = 'acknowledged', 'back to where it was';
end $$;
rollback;
\echo 'smoke_community_closure: ok'
