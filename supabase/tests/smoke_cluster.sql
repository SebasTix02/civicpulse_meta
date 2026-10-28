-- Smoke test for assign_cluster() on plain Postgres + local_shim.sql (scripts/test_db.sh).
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;
insert into devices (id, trust) values
  ('a0000000-0000-4000-8000-000000000001', 0.5), ('a0000000-0000-4000-8000-000000000002', 0.5),
  ('a0000000-0000-4000-8000-000000000003', 0.5), ('a0000000-0000-4000-8000-000000000004', 0.5);

-- A photo-checked pothole report from `dev` at (lat, lon) on network `net`.
create function pg_temp.report(dev int, lat float, lon float, net text) returns uuid language sql as $$
  insert into reports (id, device_id, city_id, geom, ip_prefix, status, domain_id, category_id, catalog_version,
                       severity, consistency_score)
  values (gen_random_uuid(), ('a0000000-0000-4000-8000-00000000000' || dev)::uuid, 'ambato',
          st_setsrid(st_makepoint(lon, lat), 4326)::geography, net, 'vision_done', 'roads', 'pothole', 1, 3, 1.0)
  returning id $$;

do $$
declare a jsonb; b jsonb; x jsonb; first_cluster uuid;
begin
  a := assign_cluster(pg_temp.report(1, -1.24170, -78.61970, '190.1.1.0/24'));
  assert (a ->> 'new_cluster')::boolean and a ->> 'status' = 'candidate', a::text;
  first_cluster := (a ->> 'cluster_id')::uuid;

  -- The same device again (spam) and a second phone on the same Wi-Fi add little support.
  perform assign_cluster(pg_temp.report(1, -1.24171, -78.61971, '190.1.1.0/24'));
  perform assign_cluster(pg_temp.report(1, -1.24172, -78.61969, '190.1.1.0/24'));
  b := assign_cluster(pg_temp.report(2, -1.24169, -78.61972, '190.1.1.0/24'));
  assert (b ->> 'cluster_id')::uuid = first_cluster, 'joins the nearby cluster';
  assert (b ->> 'distinct_devices')::int = 2 and (b ->> 'weighted_support')::real = 0.5, b::text;
  assert b ->> 'status' = 'candidate', 'one network is not consensus';

  b := assign_cluster(pg_temp.report(3, -1.24175, -78.61966, '181.2.2.0/24'));
  assert (b ->> 'weighted_support')::real = 1.0 and b ->> 'status' = 'candidate', b::text;
  b := assign_cluster(pg_temp.report(4, -1.24166, -78.61974, '200.3.3.0/24'));
  assert b ->> 'status' = 'validated' and (b ->> 'validated_now')::boolean, b::text;
  assert (b ->> 'report_count')::int = 6 and (b ->> 'weighted_support')::real = 1.5, b::text;
  assert (select count(*) from cluster_events where cluster_id = first_cluster) = 2, 'candidate + validated events';

  -- 500 m away: another cluster. A report already clustered is skipped.
  x := assign_cluster(pg_temp.report(2, -1.24620, -78.61970, '190.1.1.0/24'));
  assert (x ->> 'cluster_id')::uuid <> first_cluster, 'far report opens its own cluster';
  assert (assign_cluster((x ->> 'report_id')::uuid) ->> 'skipped')::boolean, 'idempotent';
  assert (select status from reports where id = (x ->> 'report_id')::uuid) = 'clustered', 'report moved';
end $$;

-- Simulator: only in a sandbox session. A spammer (1 device x 5) and a Wi-Fi of 4 phones stay
-- candidates; 3 neighbours on their own networks validate.
do $$
declare sid uuid; spam jsonb; wifi jsonb; ok jsonb;
begin
  insert into sandbox_sessions (city_id) values ('ambato') returning id into sid;
  spam := simulate_reports(sid, 'pothole', -1.2400, -78.6200, p_devices => 1, p_reports_each => 5);
  assert spam -> 'last' ->> 'status' = 'candidate' and (spam -> 'last' ->> 'report_count')::int = 5, spam::text;
  wifi := simulate_reports(sid, 'garbage', -1.2400, -78.6200, p_devices => 4, p_network => '10.0.0.0/24');
  assert wifi -> 'last' ->> 'status' = 'candidate' and (wifi -> 'last' ->> 'weighted_support')::real = 0.5, wifi::text;
  ok := simulate_reports(sid, 'informal_stop', -1.2400, -78.6200, p_devices => 3);
  assert ok -> 'last' ->> 'status' = 'validated', ok::text;
  assert (select bool_and(is_sandbox) from clusters where sandbox_session_id = sid), 'sandbox clusters only';
  assert not exists (select 1 from clusters where id = (ok -> 'last' ->> 'cluster_id')::uuid and not is_sandbox);
  begin
    perform simulate_reports(gen_random_uuid(), 'pothole', -1.24, -78.62);
    raise exception 'expected sandbox_session_not_found';
  exception when sqlstate 'PT404' then null;
  end;
end $$;
rollback;
\echo 'smoke_cluster: ok'
