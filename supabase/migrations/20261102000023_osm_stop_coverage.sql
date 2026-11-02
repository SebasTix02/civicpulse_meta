-- De facto stops, second pass after the first OpenStreetMap import (759 nodes, 195 with a name).
-- OSM is a community map, not the transit office's register: it is dense downtown and sparse in the
-- rural parishes. Two consequences:
--
-- 1. A case far from every mapped stop is more likely an unmapped area than a de facto stop. A case
--    counts as de facto only between min_dist_m (200) and max_dist_m (1000); beyond that the map
--    says the area has no mapped stops nearby instead of flagging it.
-- 2. A re-import must also drop stops that were deleted or merged in OSM, so upsert_official_stops
--    now replaces the source's stops for the city instead of only adding to them.
--
-- scripts/import_osm_stops.py also merges nodes closer than 15 m (a pole and its platform mapped
-- as two nodes are one stop).

set search_path = public, extensions;

insert into app_config (key, value) values
  ('de_facto_rules', '{"min_dist_m": 200, "max_dist_m": 1000, "categories": ["informal_stop", "crowded_stop"]}')
on conflict (key) do update set value = jsonb_build_object('max_dist_m', 1000) || app_config.value
 where not app_config.value ? 'max_dist_m';

create or replace function is_de_facto_stop(p_domain text, p_category text, p_dist real)
returns boolean language sql stable set search_path = public as $$
  select p_domain = 'transit' and p_dist is not null
     and p_category in (select jsonb_array_elements_text(r.value -> 'categories'))
     and p_dist > (r.value ->> 'min_dist_m')::real
     and p_dist <= coalesce((r.value ->> 'max_dist_m')::real, 'infinity')
    from (select coalesce((select value from app_config where key = 'de_facto_rules'),
                          '{"min_dist_m": 200, "max_dist_m": 1000, "categories": ["informal_stop", "crowded_stop"]}') as value) r;
$$;

-- Same signature as migration 22. Rows: [{ref, name, lat, lon}]. An empty list changes nothing
-- (a failed download must not wipe the layer); otherwise the source's stops for the city become
-- exactly these rows.
create or replace function upsert_official_stops(p_city text, p_source text, p_rows jsonb)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  n_stops   int;
  n_removed int := 0;
  n_cases   int;
begin
  if not exists (select 1 from cities where id = p_city) then
    raise exception 'city_not_found' using errcode = 'PT404';
  end if;
  if jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception 'rows_not_array' using errcode = 'PT422';
  end if;
  insert into geo_features (city_id, layer, source, source_ref, name, official, geom)
  select p_city, 'transit_stops', p_source, x ->> 'ref', nullif(x ->> 'name', ''), true,
         st_setsrid(st_makepoint((x ->> 'lon')::float8, (x ->> 'lat')::float8), 4326)::geography
    from jsonb_array_elements(p_rows) x
   where x ->> 'ref' is not null and (x ->> 'lat')::float8 between -90 and 90 and (x ->> 'lon')::float8 between -180 and 180
  on conflict (source_ref) do update set name = excluded.name, geom = excluded.geom, official = true;
  get diagnostics n_stops = row_count;

  if jsonb_array_length(p_rows) > 0 then
    delete from geo_features g
     where g.city_id = p_city and g.layer = 'transit_stops' and g.source = p_source
       and g.source_ref not in (select x ->> 'ref' from jsonb_array_elements(p_rows) x where x ->> 'ref' is not null);
    get diagnostics n_removed = row_count;
  end if;

  update clusters c
     set dist_to_official_stop_m = (
           select st_distance(c.centroid, g.geom) from geo_features g
            where g.city_id = c.city_id and g.layer = 'transit_stops' and g.official
            order by g.geom <-> c.centroid limit 1)
   where c.city_id = p_city and c.domain_id = 'transit';
  get diagnostics n_cases = row_count;
  return jsonb_build_object('stops', n_stops, 'removed', n_removed, 'transit_cases', n_cases);
end $$;
revoke all on function upsert_official_stops(text, text, jsonb) from public;
grant execute on function upsert_official_stops(text, text, jsonb) to service_role;
