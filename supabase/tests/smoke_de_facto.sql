-- Smoke test for de facto stops (migration 22) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

-- Two validated transit cases: one about 330 m from the stop, one about 55 m; and a pothole far away.
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices) values
  ('b9000000-0000-4000-8000-000000000001', 'ambato', 'transit', 'informal_stop',
   st_setsrid(st_makepoint(-78.6200, -1.2430), 4326)::geography, 'validated', 3, 3),
  ('b9000000-0000-4000-8000-000000000002', 'ambato', 'transit', 'crowded_stop',
   st_setsrid(st_makepoint(-78.6200, -1.2405), 4326)::geography, 'validated', 3, 3),
  ('b9000000-0000-4000-8000-000000000003', 'ambato', 'roads', 'pothole',
   st_setsrid(st_makepoint(-78.6300, -1.2600), 4326)::geography, 'validated', 3, 3);

do $$
declare
  x jsonb;
begin
  assert (select de_facto_stop from public_clusters where id = 'b9000000-0000-4000-8000-000000000001') = false,
    'no stops loaded yet: no distance, no flag';
  x := upsert_official_stops('ambato', 'osm', '[{"ref": "osm:node/1", "name": "Parada Test", "lat": -1.2400, "lon": -78.6200},
                                                {"ref": "osm:node/2", "lat": 95, "lon": 0}]');
  assert x = '{"stops": 1, "transit_cases": 2}'::jsonb, x::text;
  x := upsert_official_stops('ambato', 'osm', '[{"ref": "osm:node/1", "name": "Parada Test 2", "lat": -1.2400, "lon": -78.6200}]');
  assert (select count(*) from geo_features where source_ref = 'osm:node/1') = 1, 'upsert, not duplicate';
  assert (select name from public_official_stops where name like 'Parada Test%') = 'Parada Test 2';
  assert (select dist_to_official_stop_m between 300 and 360 from clusters where id = 'b9000000-0000-4000-8000-000000000001');
  assert (select de_facto_stop from public_clusters where id = 'b9000000-0000-4000-8000-000000000001'), 'far: de facto';
  assert not (select de_facto_stop from public_clusters where id = 'b9000000-0000-4000-8000-000000000002'), 'near: not';
  assert not (select de_facto_stop from public_clusters where id = 'b9000000-0000-4000-8000-000000000003'), 'not transit';
  assert (select dist_to_official_stop_m from clusters where id = 'b9000000-0000-4000-8000-000000000003') is null;
  begin perform upsert_official_stops('nowhere', 'osm', '[]'); assert false; exception when others then assert sqlerrm = 'city_not_found'; end;
end $$;
rollback;
\echo 'smoke_de_facto: ok'
