-- Smoke test for the judges' sandbox (migration 20) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

insert into sandbox_samples (id, category_id, title, suggested_text, before_path, after_path) values
  ('pothole_pair', 'pothole', '{"es": "Bache", "en": "Pothole"}', '{"es": "Bache grande", "en": "Big pothole"}',
   'public/samples/pothole_pair/before.jpg', 'public/samples/pothole_pair/after.jpg');

do $$
declare
  a   constant text := '{"sub": "5a000000-0000-4000-8000-00000000000a", "role": "authenticated", "is_anonymous": true}';
  b   constant text := '{"sub": "5a000000-0000-4000-8000-00000000000b", "role": "authenticated", "is_anonymous": true}';
  rep constant uuid := '5d000000-0000-4000-8000-000000000001';
  x   jsonb;
  n   jsonb;
  ses uuid;
  cl  uuid;
  res uuid;
  ph  uuid;
begin
  -- A visitor with no session sees nothing yet, then opens one (twice: same session).
  perform set_config('request.jwt.claims', a, true);
  assert sandbox_state() -> 'session' = 'null'::jsonb, 'no session yet';
  x := start_sandbox_session();
  ses := (x -> 'session' ->> 'id')::uuid;
  assert x -> 'quota' ->> 'camera_reports_left' = '5' and x -> 'quota' ->> 'samples_left' = '10', x::text;
  assert (start_sandbox_session() -> 'session' ->> 'id')::uuid = ses, 'same session on the second call';
  assert (select is_sandbox and sandbox_session_id = ses from devices where id = '5a000000-0000-4000-8000-00000000000a');

  -- Report with a sample: no upload, the sample's file, a spent nonce, counted as a sample.
  x := submit_report(rep, '{"lat": -1.2417, "lon": -78.6197}', 'en', p_sample_id => 'pothole_pair', p_raw_text => 'Big pothole');
  assert x ->> 'is_sandbox' = 'true', x::text;
  select p.id into ph from report_photos l join photos p on p.id = l.photo_id where l.report_id = rep;
  assert (select storage_path from photos where id = ph) = 'public/samples/pothole_pair/before.jpg';
  x := sandbox_state();
  assert x -> 'quota' ->> 'samples_left' = '9' and x -> 'quota' ->> 'camera_reports_left' = '5', x::text;
  assert x -> 'reports' -> 0 ->> 'sample_id' = 'pothole_pair', x::text;
  begin
    perform submit_report(gen_random_uuid(), '{"lat": -1.24, "lon": -78.62}', 'en', p_sample_id => 'nope');
    assert false, 'unknown sample';
  exception when sqlstate 'PT422' then null;
  end;

  -- A sample photo is never a reused photo, even when another report already used it.
  assert (check_photo_reuse(ph, 'abc', '0123456789abcdef') ->> 'reused') = 'false';

  -- The pipeline (here by hand) checks the photo and puts the report on the map.
  update reports set status = 'vision_done', domain_id = 'roads', category_id = 'pothole', catalog_version = 1,
                     severity = 3, consistency_score = 1.0, intake_status = 'REPORT_CONFIRMED_BY_PHOTO' where id = rep;
  x := assign_cluster(rep);
  cl := (x ->> 'cluster_id')::uuid;
  assert x ->> 'status' = 'candidate', x::text;

  -- Three simulated neighbours validate it; the case goes to the sandbox stakeholder. Once only.
  x := sandbox_simulate_neighbours(rep);
  assert x ->> 'status' = 'validated' and x ->> 'already' = 'false', x::text;
  assert (sandbox_simulate_neighbours(rep) ->> 'already') = 'true';
  assert (select stakeholder_id from dispatches where cluster_id = cl) = '00000000-0000-4000-8000-0000000000a1';
  update dispatches set brief = '{"title": "Bache"}' where cluster_id = cl;
  x := sandbox_state();
  assert x -> 'cases' -> 0 ->> 'simulated_reports' = '3' and x -> 'cases' -> 0 -> 'dispatch' ->> 'status' = 'sent', x::text;

  -- Closing needs the case accepted first.
  begin
    perform sandbox_submit_closure(cl, 'pothole_pair');
    assert false, 'accept first';
  exception when sqlstate 'PT409' then null;
  end;
  assert (sandbox_accept(cl) ->> 'cluster_status') = 'acknowledged';
  assert (sandbox_accept(cl) ->> 'status') = 'accepted', 'accepting twice is fine';

  -- Another visitor cannot touch this case.
  perform set_config('request.jwt.claims', b, true);
  perform start_sandbox_session();
  begin
    perform sandbox_accept(cl);
    assert false, 'other session';
  exception when sqlstate 'PT404' then null;
  end;
  assert jsonb_array_length(sandbox_state() -> 'cases') = 0;

  -- Back to the first visitor: the AFTER half of the pair, on the BEFORE report's pin.
  perform set_config('request.jwt.claims', a, true);
  x := sandbox_submit_closure(cl, 'pothole_pair');
  res := (x ->> 'resolution_id')::uuid;
  assert x ->> 'cluster_status' = 'resolution_pending', x::text;
  assert (select before_report_id = rep and closure_path = 'community' and geom is not null
            and stakeholder_id = '00000000-0000-4000-8000-0000000000a1' from resolutions where id = res);
  assert (sandbox_state() -> 'quota' ->> 'samples_left') = '8';
  begin
    perform sandbox_submit_closure(cl, 'pothole_pair');
    assert false, 'one open closure at a time';
  exception when sqlstate 'PT409' then null;
  end;

  -- Review: not before the AI answers; against the AI only with a note.
  begin
    perform sandbox_review_closure(res, 'approve');
    assert false, 'ai pending';
  exception when sqlstate 'PT409' then null;
  end;
  update resolutions set ai_status = 'PERSISTENT_ISSUE' where id = res;
  begin
    perform sandbox_review_closure(res, 'approve');
    assert false, 'note required';
  exception when sqlstate 'PT422' then null;
  end;
  assert (sandbox_review_closure(res, 'reject') ->> 'cluster_status') = 'acknowledged';

  -- Second try with a camera photo, then approved with the AI.
  n := issue_capture_nonce('community_resolution');
  assert n ->> 'bucket' = 'sandbox' and can_upload_capture('sandbox', n ->> 'upload_path'), n::text;
  x := sandbox_submit_closure(cl, p_nonce => n ->> 'nonce', p_photo_path => n ->> 'upload_path');
  res := (x ->> 'resolution_id')::uuid;
  assert (select geom is null and before_report_id = rep from resolutions where id = res);
  assert (sandbox_state() -> 'quota' ->> 'camera_closures_left') = '1';
  update resolutions set ai_status = 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW' where id = res;
  assert (sandbox_review_closure(res, 'approve') ->> 'cluster_status') = 'resolved';
  x := sandbox_state();
  assert x -> 'cases' -> 0 ->> 'status' = 'resolved' and x -> 'cases' -> 0 -> 'dispatch' ->> 'status' = 'closed', x::text;

  -- Camera quota: with the limit at 0 a camera report is refused, samples still work.
  update app_config set value = value || '{"camera_reports": 0}' where key = 'sandbox_limits';
  n := issue_capture_nonce('report');
  begin
    perform submit_report(gen_random_uuid(), '{"lat": -1.24, "lon": -78.62}', 'en', n ->> 'nonce', n ->> 'upload_path');
    assert false, 'camera quota';
  exception when sqlstate 'PT429' then
    assert sqlerrm = 'sandbox_quota_exceeded', sqlerrm;
  end;
  perform submit_report(gen_random_uuid(), '{"lat": -1.24, "lon": -78.62}', 'en', p_sample_id => 'pothole_pair');
  update app_config set value = value || '{"global_reports": 0}' where key = 'sandbox_limits';
  begin
    perform submit_report(gen_random_uuid(), '{"lat": -1.24, "lon": -78.62}', 'en', p_sample_id => 'pothole_pair');
    assert false, 'global cap';
  exception when sqlstate 'PT429' then
    assert sqlerrm = 'sandbox_daily_cap', sqlerrm;
  end;

  -- A citizen device cannot use samples or open a sandbox session.
  perform set_config('request.jwt.claims',
    '{"sub": "5a000000-0000-4000-8000-00000000000c", "role": "authenticated", "is_anonymous": true}', true);
  perform issue_capture_nonce('report');  -- creates the citizen device
  begin
    perform submit_report(gen_random_uuid(), '{"lat": -1.24, "lon": -78.62}', 'en', p_sample_id => 'pothole_pair');
    assert false, 'citizens have no samples';
  exception when sqlstate 'PT422' then null;
  end;
  begin
    perform start_sandbox_session();
    assert false, 'citizen device';
  exception when sqlstate 'PT409' then null;
  end;

  -- Cleanup: an expired session leaves no rows behind and names its resolutions for Storage.
  update sandbox_sessions set expires_at = now() - interval '1 minute' where id = ses;
  x := sandbox_cleanup();
  assert x -> 'sessions' ? ses::text and jsonb_array_length(x -> 'resolutions') = 2, x::text;
  assert not exists (select 1 from reports where sandbox_session_id = ses);
  assert not exists (select 1 from clusters where id = cl);
  assert not exists (select 1 from devices where id = '5a000000-0000-4000-8000-00000000000a');
  assert not exists (select 1 from photos where id = ph);
  assert not exists (select 1 from resolutions where id = res);
  perform set_config('request.jwt.claims', a, true);
  assert sandbox_state() -> 'session' = 'null'::jsonb;
  assert (start_sandbox_session() -> 'session' ->> 'id')::uuid <> ses, 'a fresh session after cleanup';
end $$;
rollback;
\echo 'smoke_sandbox: ok'
