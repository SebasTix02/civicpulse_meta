-- Case priority from where the problem is and what the report says (plan §6, second weights).
--
-- assign_cluster() and refresh_cluster_after_moderation() keep computing the base score (devices,
-- support, severity, photo evidence, days open). A BEFORE trigger on clusters now adds bonuses:
--   * road: the most important road within priority_rules.road_m (OSM trunk/primary/secondary/tertiary);
--   * bus route: a bus route relation within bus_route_m;
--   * places: a school, hospital or market within poi_m;
--   * centre: inside the city centre radius;
--   * text: risk signals in the visible reports' spec (models.RISK_SIGNALS: injury, accident, ...),
--     capped at signals_cap. "near_school"/"near_hospital" from the text only count when the map
--     has no such place nearby (the map already counted it).
-- clusters.priority_context keeps every reason, so the panel can say why a case is on top.
-- The layers come from scripts/import_osm_context.py through replace_geo_layer().

set search_path = public, extensions;

insert into app_config (key, value) values
  ('priority_rules', '{
     "road_m": 25,
     "road_class": {"trunk": 1.5, "primary": 1.5, "secondary": 1.0, "tertiary": 0.6},
     "bus_route_m": 30, "bus_route": 0.8,
     "poi_m": 150, "poi": {"schools": 0.8, "hospitals": 0.8, "markets": 0.4},
     "centres": {"ambato": {"lat": -1.2417, "lon": -78.6197, "radius_m": 1500, "bonus": 0.5}},
     "signals": {"injury": 1.2, "accident": 1.0, "blocks_traffic": 0.6, "vehicle_damage": 0.6,
                 "near_school": 0.4, "near_hospital": 0.4, "night_hazard": 0.4, "flooding": 0.3,
                 "recurring": 0.3},
     "signals_cap": 2.0}')
on conflict (key) do nothing;

alter table clusters add column priority_context jsonb;

-- Every reason for one cluster, without the base. Pure read: safe inside the trigger.
create or replace function cluster_priority_context(p_cluster uuid, p_city text, p_centroid geography)
returns jsonb language plpgsql stable security definer set search_path = public, extensions as $$
declare
  rules    jsonb := coalesce((select value from app_config where key = 'priority_rules'), '{}');
  v_road   record;
  v_bus    record;
  v_near   jsonb;
  v_kinds  text[];
  v_centre jsonb := rules -> 'centres' -> p_city;
  v_centre_m int;
  v_signals text[];
  v_counted text[];
  b_road real := 0; b_bus real := 0; b_near real := 0; b_centre real := 0; b_text real := 0;
begin
  select g.name, g.props ->> 'class' as class, round(st_distance(g.geom, p_centroid))::int as m,
         coalesce((rules -> 'road_class' ->> (g.props ->> 'class'))::real, 0) as w
    into v_road
    from geo_features g
   where g.city_id = p_city and g.layer = 'roads'
     and st_dwithin(g.geom, p_centroid, coalesce((rules ->> 'road_m')::real, 25))
   order by coalesce((rules -> 'road_class' ->> (g.props ->> 'class'))::real, 0) desc, st_distance(g.geom, p_centroid)
   limit 1;
  b_road := coalesce(v_road.w, 0);

  select g.name, g.props ->> 'ref' as ref, round(st_distance(g.geom, p_centroid))::int as m into v_bus
    from geo_features g
   where g.city_id = p_city and g.layer = 'transit_routes'
     and st_dwithin(g.geom, p_centroid, coalesce((rules ->> 'bus_route_m')::real, 30))
   order by st_distance(g.geom, p_centroid) limit 1;
  if v_bus.m is not null then
    b_bus := coalesce((rules ->> 'bus_route')::real, 0);
  end if;

  -- Nearest place of each kind; each kind counts once.
  select coalesce(jsonb_agg(jsonb_build_object('kind', layer, 'name', name, 'm', m,
                                    'bonus', coalesce((rules -> 'poi' ->> layer)::real, 0)) order by m), '[]'),
         coalesce(array_agg(layer), '{}'), coalesce(sum(coalesce((rules -> 'poi' ->> layer)::real, 0)), 0)
    into v_near, v_kinds, b_near
    from (select distinct on (g.layer) g.layer, g.name, round(st_distance(g.geom, p_centroid))::int as m
            from geo_features g
           where g.city_id = p_city and g.layer in ('schools', 'hospitals', 'markets')
             and st_dwithin(g.geom, p_centroid, coalesce((rules ->> 'poi_m')::real, 150))
           order by g.layer, st_distance(g.geom, p_centroid)) p;

  if v_centre is not null then
    v_centre_m := round(st_distance(p_centroid,
                    st_setsrid(st_makepoint((v_centre ->> 'lon')::float8, (v_centre ->> 'lat')::float8), 4326)::geography))::int;
    if v_centre_m <= coalesce((v_centre ->> 'radius_m')::real, 0) then
      b_centre := coalesce((v_centre ->> 'bonus')::real, 0);
    end if;
  end if;

  select coalesce(array_agg(distinct s order by s), '{}') into v_signals
    from reports r, jsonb_array_elements_text(case when jsonb_typeof(r.spec -> 'risk_signals') = 'array'
                                                   then r.spec -> 'risk_signals' else '[]' end) s
   where r.cluster_id = p_cluster and r.moderation_status = 'visible';
  v_counted := array(select s from unnest(v_signals) s
                      where not (s = 'near_school' and 'schools' = any (v_kinds))
                        and not (s = 'near_hospital' and 'hospitals' = any (v_kinds)));
  select least(coalesce((rules ->> 'signals_cap')::real, 2),
               coalesce(sum(coalesce((rules -> 'signals' ->> s)::real, 0)), 0))
    into b_text from unnest(v_counted) s;

  return jsonb_build_object(
    'road', case when v_road.m is null then null
                 else jsonb_build_object('class', v_road.class, 'name', v_road.name, 'm', v_road.m) end,
    'bus_route', case when v_bus.m is null then null
                      else jsonb_build_object('name', v_bus.name, 'ref', v_bus.ref, 'm', v_bus.m) end,
    'near', v_near,
    'centre_m', v_centre_m,
    'in_centre', b_centre > 0,
    'signals', to_jsonb(v_signals),
    'bonus', jsonb_build_object('road', b_road, 'bus_route', b_bus, 'near', b_near, 'centre', b_centre,
                                'signals', round(b_text::numeric, 2)),
    'bonus_total', round((b_road + b_bus + b_near + b_centre + b_text)::numeric, 3));
end $$;
revoke all on function cluster_priority_context(uuid, text, geography) from public, anon, authenticated;

-- The writers set priority_score to the base. When they do (or on insert), that value becomes
-- the base; otherwise the previous base is kept. The stored score is always base + bonuses.
create or replace function clusters_priority() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
declare
  v_base real;
  v_ctx  jsonb;
begin
  if tg_op = 'INSERT' or new.priority_score is distinct from old.priority_score then
    v_base := new.priority_score;
  else
    v_base := coalesce((old.priority_context ->> 'base')::real, old.priority_score);
  end if;
  v_ctx := cluster_priority_context(new.id, new.city_id, new.centroid);
  new.priority_context := v_ctx || jsonb_build_object('base', v_base);
  new.priority_score := round((v_base + (v_ctx ->> 'bonus_total')::real)::numeric, 3);
  return new;
end $$;

create trigger clusters_priority before insert or update of
  report_count, distinct_devices, weighted_support, centroid, priority_score, priority_context, moderation_status
  on clusters for each row execute function clusters_priority();

-- Recompute every case of a city (after loading layers or changing priority_rules). Setting the
-- context to null fires the trigger, which keeps each base.
create or replace function refresh_city_priorities(p_city text) returns int
language plpgsql volatile security definer set search_path = public, extensions as $$
declare n int;
begin
  update clusters set priority_context = null where city_id = p_city;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function refresh_city_priorities(text) from public, anon, authenticated;
grant execute on function refresh_city_priorities(text) to service_role;

-- Load one context layer in batches. rows: [{ref, name, props, geojson}]; `first` = true on the
-- first batch deletes what this source had in that layer for the city. An empty first batch is
-- refused, so a failed download never wipes a layer. Refs are stored as "<source>:<layer>:<ref>".
create or replace function replace_geo_layer(p_city text, p_layer text, p_source text, p_rows jsonb, p_first boolean)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  n_removed int := 0;
  n_rows    int;
begin
  if not exists (select 1 from cities where id = p_city) then
    raise exception 'city_not_found' using errcode = 'PT404';
  end if;
  if p_layer not in ('roads', 'transit_routes', 'schools', 'hospitals', 'markets') then
    raise exception 'layer_not_allowed' using errcode = 'PT422';
  end if;
  if jsonb_typeof(p_rows) is distinct from 'array' or (p_first and jsonb_array_length(p_rows) = 0) then
    raise exception 'rows_empty' using errcode = 'PT422';
  end if;
  if p_first then
    delete from geo_features where city_id = p_city and layer = p_layer and source = p_source;
    get diagnostics n_removed = row_count;
  end if;
  insert into geo_features (city_id, layer, source, source_ref, name, official, geom, props)
  select p_city, p_layer, p_source, p_source || ':' || p_layer || ':' || (x ->> 'ref'), nullif(x ->> 'name', ''),
         false, st_setsrid(st_geomfromgeojson(x -> 'geojson'), 4326)::geography, coalesce(x -> 'props', '{}')
    from jsonb_array_elements(p_rows) x
   where x ->> 'ref' is not null and x -> 'geojson' is not null
  on conflict (source_ref) do update set name = excluded.name, geom = excluded.geom, props = excluded.props;
  get diagnostics n_rows = row_count;
  return jsonb_build_object('layer', p_layer, 'rows', n_rows, 'removed', n_removed);
end $$;
revoke all on function replace_geo_layer(text, text, text, jsonb, boolean) from public, anon, authenticated;
grant execute on function replace_geo_layer(text, text, text, jsonb, boolean) to service_role;

-- The panel shows the reasons next to the score (same view as migration 15, one column more).
create or replace view panel_inbox with (security_invoker = false) as
select d.cluster_id, d.stakeholder_id, s.name as stakeholder_name, d.is_sandbox,
       d.status as dispatch_status, d.created_at as dispatched_at, d.seen_at, d.note, d.brief,
       c.priority_score,
       jsonb_build_object(
         'id', c.id, 'city_id', c.city_id, 'domain_id', c.domain_id, 'category_id', c.category_id,
         'category_name', cat.display_name,
         'lat', st_y(c.centroid::geometry), 'lon', st_x(c.centroid::geometry),
         'report_count', c.report_count, 'distinct_devices', c.distinct_devices, 'status', c.status,
         'validated_at', c.validated_at, 'last_report_at', c.last_report_at, 'resolved_at', c.resolved_at) as cluster,
       (select jsonb_build_object(
                 'id', r.id, 'ai_status', r.ai_status, 'outcome', r.outcome, 'ai_overridden', r.ai_overridden,
                 'created_at', r.created_at, 'decided_at', r.decided_at,
                 'after_public_path', p.public_path, 'verification', r.verification,
                 'can_review', member_can('resolutions.approve', r.stakeholder_id),
                 'review_note', (select rr.note from resolution_reviews rr
                                  where rr.resolution_id = r.id order by rr.created_at desc limit 1))
          from resolutions r join photos p on p.id = r.photo_id
         where r.cluster_id = d.cluster_id and r.stakeholder_id = d.stakeholder_id
         order by r.created_at desc limit 1) as latest_resolution,
       member_can('resolutions.submit', d.stakeholder_id) as can_submit_resolution,
       c.priority_context
  from dispatches d
  join stakeholders s on s.id = d.stakeholder_id
  join clusters c on c.id = d.cluster_id
  join categories cat on cat.id = c.category_id
 where d.brief is not null and c.moderation_status = 'visible'
   and member_can('dispatches.read', d.stakeholder_id);

-- Existing cases get their context now (no layers yet: centre and text only).
update clusters set priority_context = null;
