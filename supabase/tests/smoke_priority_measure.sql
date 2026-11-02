-- Smoke test for size and depth in the case priority (migrations 28 and 29: depth high 0.5, medium 0.25) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

insert into devices (id, trust) values ('a8000000-0000-4000-8000-000000000001', 0.5), ('a8000000-0000-4000-8000-000000000002', 0.5);
-- On the outskirts: no centre, no layers. Base 4.
insert into clusters (id, city_id, domain_id, category_id, centroid, status, report_count, distinct_devices, priority_score) values
  ('b8000000-0000-4000-8000-000000000001', 'ambato', 'roads', 'pothole',
   st_setsrid(st_makepoint(-78.6000, -1.2000), 4326)::geography, 'validated', 2, 2, 4.0);
insert into reports (id, device_id, city_id, geom, status, domain_id, category_id, catalog_version, severity, cluster_id, intake)
values
  ('d8000000-0000-4000-8000-000000000001', 'a8000000-0000-4000-8000-000000000001', 'ambato',
   st_setsrid(st_makepoint(-78.6000, -1.2000), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
   'b8000000-0000-4000-8000-000000000001',
   '{"measure": {"size": {"status": "ok", "width_share": 0.9, "size_class": "large", "clipped": true},
                 "depth": {"level": "medium"}}}'),
  ('d8000000-0000-4000-8000-000000000002', 'a8000000-0000-4000-8000-000000000002', 'ambato',
   st_setsrid(st_makepoint(-78.6000, -1.2000), 4326)::geography, 'clustered', 'roads', 'pothole', 1, 3,
   'b8000000-0000-4000-8000-000000000001',
   '{"measure": {"size": {"status": "ok", "width_share": 0.2, "size_class": "medium", "clipped": false},
                 "depth": {"level": "high"}}}');

do $$
declare
  c1 uuid := 'b8000000-0000-4000-8000-000000000001';
  ctx jsonb;
begin
  update clusters set report_count = 2 where id = c1;  -- what assign_cluster does after the vision stage
  ctx := (select priority_context from clusters where id = c1);
  -- The unclipped measure wins over the larger clipped one; the deepest level counts.
  assert ctx -> 'size' ->> 'class' = 'medium' and not (ctx -> 'size' ->> 'clipped')::boolean, ctx::text;
  assert ctx ->> 'depth' = 'high', ctx::text;
  assert (ctx -> 'bonus' ->> 'size')::numeric = 0.5 and (ctx -> 'bonus' ->> 'depth')::numeric = 0.5, ctx::text;
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 5.0;

  -- Only the clipped photo left: shown, adds nothing; depth falls to medium.
  update reports set moderation_status = 'hidden' where id = 'd8000000-0000-4000-8000-000000000002';
  update clusters set report_count = 1 where id = c1;
  ctx := (select priority_context from clusters where id = c1);
  assert (ctx -> 'size' ->> 'clipped')::boolean and (ctx -> 'bonus' ->> 'size')::numeric = 0, ctx::text;
  assert (select round(priority_score::numeric, 2) from clusters where id = c1) = 4.25;
  assert (ctx ->> 'base')::numeric = 4.0;
end $$;
rollback;
\echo 'smoke_priority_measure: ok'
