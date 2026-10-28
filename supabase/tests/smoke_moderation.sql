-- Smoke test for the moderation checks (photo reuse, impossible GPS) on plain Postgres.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;
insert into devices (id) values ('b0000000-0000-4000-8000-000000000001'), ('b0000000-0000-4000-8000-000000000002');

-- A real report with its photo, from device `dev`, `mins` minutes ago at (lat, lon).
create function pg_temp.report_with_photo(dev int, lat float, lon float, mins int default 0) returns uuid language plpgsql as $$
declare v_report uuid := gen_random_uuid(); v_photo uuid; v_nonce text := gen_random_uuid()::text;
begin
  insert into capture_nonces (nonce, device_id) values (v_nonce, ('b0000000-0000-4000-8000-00000000000' || dev)::uuid);
  insert into photos (storage_path) values ('raw/' || v_nonce || '.jpg') returning id into v_photo;
  insert into reports (id, device_id, city_id, geom, gps_geom, current_photo_version, created_at)
  values (v_report, ('b0000000-0000-4000-8000-00000000000' || dev)::uuid, 'ambato',
          st_setsrid(st_makepoint(lon, lat), 4326)::geography, st_setsrid(st_makepoint(lon, lat), 4326)::geography,
          1, now() - make_interval(mins => mins));
  insert into report_photos (report_id, version, photo_id, capture_nonce) values (v_report, 1, v_photo, v_nonce);
  return v_report;
end $$;

create function pg_temp.photo_of(p_report uuid) returns uuid language sql as $$
  select photo_id from report_photos where report_id = p_report and version = 1 $$;

do $$
declare a uuid; b uuid; c uuid; res jsonb;
begin
  a := pg_temp.report_with_photo(1, -1.2417, -78.6197);
  b := pg_temp.report_with_photo(2, -1.2418, -78.6198);
  c := pg_temp.report_with_photo(2, -1.2419, -78.6199);
  res := check_photo_reuse(pg_temp.photo_of(a), 'aaaa', 'bf01c179498dee83');
  assert not (res ->> 'reused')::boolean, 'first photo is new';
  res := check_photo_reuse(pg_temp.photo_of(b), 'bbbb', 'bf01c179498dee8c');  -- 4 bits away: same picture
  assert (res ->> 'reused')::boolean and (res -> 'matches' -> 0 ->> 'distance')::int = 4, res::text;
  assert (res -> 'matches' -> 0 ->> 'report_id')::uuid = a, 'points at the original';
  res := check_photo_reuse(pg_temp.photo_of(c), 'cccc', '40fe3e86b6721170');  -- the inverse: a different picture
  assert not (res ->> 'reused')::boolean, res::text;
  perform check_photo_reuse(pg_temp.photo_of(b), 'bbbb', 'bf01c179498dee8c');  -- a retry opens no second flag
  assert (select count(*) from moderation_flags where report_id = b and reason = 'reused_photo') = 1, 'one flag';
  assert (select count(*) from moderation_flags) = 1, 'only the reused one is flagged';
end $$;

-- Impossible travel: device 1 reports in Ambato, then 10 km away 3 minutes later (200 km/h).
-- Device 2 moves 3 km in 30 minutes (6 km/h): fine.
do $$
declare far uuid; ok uuid; ev jsonb;
begin
  perform pg_temp.report_with_photo(1, -1.2417, -78.6197, 30);
  far := pg_temp.report_with_photo(1, -1.3317, -78.6197, 27);
  select evidence into ev from moderation_flags where report_id = far and reason = 'impossible_gps';
  assert ev is not null and (ev ->> 'speed_kmh')::int > 150, coalesce(ev::text, 'no flag');
  perform pg_temp.report_with_photo(2, -1.2600, -78.6197, 60);
  ok := pg_temp.report_with_photo(2, -1.2870, -78.6197, 30);
  assert not exists (select 1 from moderation_flags where report_id = ok), 'walking pace is not flagged';
end $$;
rollback;
\echo 'smoke_moderation: ok'
