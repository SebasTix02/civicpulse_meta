-- Smoke test for case priority context (migration 27) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

insert into devices (id, trust) values ('a7000000-0000-4000-8000-000000000001', 0.5);
-- One pothole next to the central park, one on the outskirts.
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices, priority_score) values
  ('b7000000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole',
   st_setsrid(st_makepoint(-78.6197, -1.2420), 4326)::geography, 'validated', 1, 1, 4.0),
  ('b7000000-0000-4000-8000-000000000002', 'ambato', 'roads', 'pothole',
   st_setsrid(st_makepoint(-78.6000, -1.2000), 4326)::geography, 'validated', 1, 1, 4.0);
insert into reports (id, device_id, city_id, geom, status, domain_id, category_id, catalog_version, severity, cluster_id, spec)
values ('d7000000-0000-4000-8000-000000000001', 'a7000000-0000-4000-8000-000000000001', 'ambato',
        st_setsrid(st_makepoint(-78.6197, -1.2420), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
        'b7000000-0000-4000-8000-000000000001', '{"risk_signals": ["injury", "near_school", "recurring"]}');

do $$
declare
  c1 uuid := 'b7000000-0000-4000-8000-000000000001';
  c2 uuid := 'b7000000-0000-4000-8000-000000000002';
  x jsonb;
  ctx jsonb;
begin
  -- Insert: base kept, centre bonus already there; the report did not exist yet, so no signals.
  ctx := (select priority_context from clusters where id = c1);
  assert (ctx ->> 'base')::real = 4.0 and (ctx ->> 'in_centre')::boolean, ctx::text;
  assert (select round(priority_score::numeric, 2) from clusters where id = c2) = 4.0, 'outskirts, no layers: base only';

  -- Layers: a primary road through the park, a residential street (not loaded: only major roads), a school 100 m away.
  begin perform replace_geo_layer('ambato', 'roads', 'osm', '[]', true); assert false;
  exception when others then assert sqlerrm = 'rows_empty'; end;
  begin perform replace_geo_layer('ambato', 'buildings', 'osm', '[{"ref": "x"}]', true); assert false;
  exception when others then assert sqlerrm = 'layer_not_allowed'; end;
  x := replace_geo_layer('ambato', 'roads', 'osm', '[
    {"ref": "way/1", "name": "Av. Test", "props": {"class": "primary"},
     "geojson": {"type": "LineString", "coordinates": [[-78.6250, -1.2421], [-78.6150, -1.2421]]}},
    {"ref": "way/2", "name": "Calle Test", "props": {"class": "tertiary"},
     "geojson": {"type": "LineString", "coordinates": [[-78.6197, -1.2500], [-78.6197, -1.2300]]}}]', true);
  assert x = '{"layer": "roads", "rows": 2, "removed": 0}'::jsonb, x::text;
  x := replace_geo_layer('ambato', 'schools', 'osm', '[{"ref": "node/9", "name": "Escuela Test",
    "geojson": {"type": "Point", "coordinates": [-78.6188, -1.2420]}}]', true);
  x := replace_geo_layer('ambato', 'transit_routes', 'osm', '[{"ref": "relation/5", "name": "Línea 1", "props": {"ref": "1"},
    "geojson": {"type": "MultiLineString", "coordinates": [[[-78.6197, -1.2500], [-78.6197, -1.2300]]]}}]', true);
  assert (select source_ref from geo_features where layer = 'schools') = 'osm:schools:node/9';
  assert refresh_city_priorities('ambato') >= 2;

  ctx := (select priority_context from clusters where id = c1);
  assert ctx -> 'road' ->> 'class' = 'primary', 'the most important road wins: ' || ctx::text;
  assert (ctx -> 'bus_route' ->> 'ref') = '1', ctx::text;
  assert ctx -> 'near' -> 0 ->> 'kind' = 'schools' and (ctx -> 'near' -> 0 ->> 'm')::int between 90 and 110, ctx::text;
  assert ctx -> 'signals' = '["injury", "near_school", "recurring"]'::jsonb, ctx::text;
  -- near_school from the text is not counted twice: injury 1.2 + recurring 0.3.
  assert (ctx -> 'bonus' ->> 'signals')::numeric = 1.5, ctx::text;
  -- 4 + road 1.5 + bus 0.8 + school 0.8 + centre 0.5 + text 1.5
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 9.1, (select priority_score from clusters where id = c1)::text;
  assert (select round(priority_score::numeric, 2) from clusters where id = c2) = 4.0;

  -- A writer sets a new base (as assign_cluster does): bonuses are added on top.
  update clusters set priority_score = 5.0 where id = c1;
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 10.1;
  -- Updates that do not touch the score keep the base.
  update clusters set report_count = 2 where id = c1;
  assert (select (priority_context ->> 'base')::real from clusters where id = c1) = 5.0;
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 10.1;

  -- Hiding the report removes its signals (refresh_cluster_after_moderation touches the counts).
  update reports set moderation_status = 'hidden' where id = 'd7000000-0000-4000-8000-000000000001';
  update clusters set report_count = 1 where id = c1;
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 8.6, (select priority_context from clusters where id = c1)::text;

  -- Re-import: the first batch replaces the layer.
  x := replace_geo_layer('ambato', 'roads', 'osm', '[{"ref": "way/3", "props": {"class": "secondary"},
    "geojson": {"type": "LineString", "coordinates": [[-78.6250, -1.2420], [-78.6150, -1.2420]]}}]', true);
  assert x ->> 'removed' = '2', x::text;
  perform refresh_city_priorities('ambato');
  assert (select priority_context -> 'road' ->> 'class' from clusters where id = c1) = 'secondary';

  -- The panel view carries the context.
  assert exists (select 1 from information_schema.columns where table_name = 'panel_inbox' and column_name = 'priority_context');
end $$;
rollback;
\echo 'smoke_priority: ok'
