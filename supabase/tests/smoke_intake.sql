-- Smoke test for the citizen intake path on plain Postgres + local_shim.sql.
-- Run: scripts/test_db.sh  (applies shim, migrations, seeds, then this file)
\set ON_ERROR_STOP 1
begin;
insert into auth.users (id) values ('11111111-1111-4111-8111-111111111111');
set local role authenticated;
select null from set_config('request.jwt.claims',
  '{"sub": "11111111-1111-4111-8111-111111111111", "role": "authenticated", "is_anonymous": true}', true);
select null from set_config('request.headers', '{"x-forwarded-for": "190.152.10.77, 10.0.0.1"}', true);

create temp table t_nonce as select issue_capture_nonce() as n;

-- First submit creates the report; the second identical call is idempotent.
do $$
declare n jsonb := (select n from t_nonce); r jsonb;
begin
  r := submit_report(
    p_report_id => '22222222-2222-4222-8222-222222222222',
    p_location => '{"lat": -1.2417, "lon": -78.6197}', p_lang => 'es',
    p_nonce => n ->> 'nonce', p_photo_path => n ->> 'upload_path',
    p_gps_location => '{"lat": -1.24171, "lon": -78.61971}', p_gps_accuracy_m => 8,
    p_raw_text => 'Hay un bache enorme frente al mercado');
  assert r ->> 'city_id' = 'ambato', 'city resolved by geofence';
  r := submit_report(
    p_report_id => '22222222-2222-4222-8222-222222222222',
    p_location => '{"lat": -1.2417, "lon": -78.6197}', p_lang => 'es',
    p_nonce => n ->> 'nonce', p_photo_path => n ->> 'upload_path');
  assert r ->> 'status' = 'received', 'idempotent retry';
end $$;

-- Pin more than 15 m away from the GPS fix is refused.
do $$
declare n jsonb := issue_capture_nonce();
begin
  perform submit_report(
    p_report_id => gen_random_uuid(), p_location => '{"lat": -1.2417, "lon": -78.6197}', p_lang => 'es',
    p_nonce => n ->> 'nonce', p_photo_path => n ->> 'upload_path',
    p_gps_location => '{"lat": -1.2427, "lon": -78.6197}', p_gps_accuracy_m => 8);
  raise exception 'expected pin_too_far';
exception when sqlstate 'PT422' then
  assert sqlerrm = 'pin_too_far', sqlerrm;
end $$;

do $$ begin
  assert (select count(*) from my_reports) = 1, 'device sees exactly its report';
end $$;
reset role;
do $$ begin
  assert (select ip_prefix from reports where id = '22222222-2222-4222-8222-222222222222') = '190.152.10.0/24', 'ip prefix /24';
  assert (select used_at is not null from capture_nonces c join report_photos p on p.capture_nonce = c.nonce
           where p.report_id = '22222222-2222-4222-8222-222222222222'), 'nonce spent';
end $$;
select get_demo_status() ->> 'level' as demo_level;
rollback;
\echo 'smoke_intake: ok'
