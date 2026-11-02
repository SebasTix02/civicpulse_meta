-- De facto bus stops (plan §5.6, the 67% figure): a validated transit case of people waiting or
-- buses stopping where there is no official stop. assign_cluster (migration 8) already stores the
-- distance to the nearest official stop in clusters.dist_to_official_stop_m; it stayed null because
-- the official stops layer was empty. scripts/import_osm_stops.py now loads it from OpenStreetMap
-- (highway=bus_stop, ODbL) through upsert_official_stops, which also refreshes the distances.
--
-- The public map gets the official stops as a layer and a de_facto_stop flag per case.

set search_path = public, extensions;

insert into app_config (key, value) values
  ('de_facto_rules', '{"min_dist_m": 200, "categories": ["informal_stop", "crowded_stop"]}')
on conflict (key) do nothing;

create or replace function is_de_facto_stop(p_domain text, p_category text, p_dist real)
returns boolean language sql stable set search_path = public as $$
  select p_domain = 'transit' and p_dist is not null
     and p_category in (select jsonb_array_elements_text(r.value -> 'categories'))
     and p_dist > (r.value ->> 'min_dist_m')::real
    from (select coalesce((select value from app_config where key = 'de_facto_rules'),
                          '{"min_dist_m": 200, "categories": ["informal_stop", "crowded_stop"]}') as value) r;
$$;

-- Same view as migration 18, one column appended.
create or replace view public_clusters with (security_invoker = false) as
select c.id, c.city_id, c.domain_id, c.category_id,
       st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lon,
       c.report_count, c.distinct_devices, c.priority_score, c.status,
       c.public_summary, c.dist_to_official_stop_m, c.validated_at, c.last_report_at, c.resolved_at,
       cat.display_name as category_name,
       (select p.public_path
          from reports r
          join report_photos rp on rp.report_id = r.id and rp.version = r.current_photo_version
          join photos p on p.id = rp.photo_id
         where r.cluster_id = c.id and r.moderation_status = 'visible'
           and r.intake_status = 'REPORT_CONFIRMED_BY_PHOTO' and p.public_path is not null
         order by r.created_at desc limit 1) as photo_public_path,
       is_de_facto_stop(c.domain_id, c.category_id, c.dist_to_official_stop_m) as de_facto_stop
  from clusters c
  join categories cat on cat.id = c.category_id
 where not c.is_sandbox and c.moderation_status = 'visible' and c.status in ('validated', 'acknowledged', 'resolution_pending', 'resolved');

create or replace view public_official_stops with (security_invoker = false) as
select g.id, g.city_id, g.name, st_y(g.geom::geometry) as lat, st_x(g.geom::geometry) as lon
  from geo_features g
 where g.layer = 'transit_stops' and g.official;
grant select on public_official_stops to anon, authenticated;

-- Loads official stops for a city (rows: [{ref, name, lat, lon}]) and recomputes the distance of
-- every transit case of that city. Returns how many stops were written and cases refreshed.
create or replace function upsert_official_stops(p_city text, p_source text, p_rows jsonb)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  n_stops int;
  n_cases int;
begin
  if not exists (select 1 from cities where id = p_city) then
    raise exception 'city_not_found' using errcode = 'PT404';
  end if;
  insert into geo_features (city_id, layer, source, source_ref, name, official, geom)
  select p_city, 'transit_stops', p_source, x ->> 'ref', nullif(x ->> 'name', ''), true,
         st_setsrid(st_makepoint((x ->> 'lon')::float8, (x ->> 'lat')::float8), 4326)::geography
    from jsonb_array_elements(p_rows) x
   where x ->> 'ref' is not null and (x ->> 'lat')::float8 between -90 and 90 and (x ->> 'lon')::float8 between -180 and 180
  on conflict (source_ref) do update set name = excluded.name, geom = excluded.geom, official = true;
  get diagnostics n_stops = row_count;

  update clusters c
     set dist_to_official_stop_m = (
           select st_distance(c.centroid, g.geom) from geo_features g
            where g.city_id = c.city_id and g.layer = 'transit_stops' and g.official
            order by g.geom <-> c.centroid limit 1)
   where c.city_id = p_city and c.domain_id = 'transit';
  get diagnostics n_cases = row_count;
  return jsonb_build_object('stops', n_stops, 'transit_cases', n_cases);
end $$;
revoke all on function upsert_official_stops(text, text, jsonb) from public;
grant execute on function upsert_official_stops(text, text, jsonb) to service_role;
