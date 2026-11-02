-- The pilot city's real boundary. The seed used a rough box of the Ambato canton that also took in
-- parts of the neighbouring cantons to the south and east (Pelileo, Cevallos, Tisaleo). Until the
-- official polygon is loaded, the box shrinks to the urban area and its parishes; then
-- scripts/import_city_boundary.py loads the canton polygon from OpenStreetMap through
-- set_city_boundary. Every report, real or sandbox, must fall inside it (outside_service_area),
-- and the map draws it and keeps the pin inside.

set search_path = public, extensions;

update cities
   set boundary = st_multi(st_makeenvelope(-78.72, -1.33, -78.55, -1.17, 4326))::geography
 where id = 'ambato'
   and st_equals(boundary::geometry, st_multi(st_makeenvelope(-78.80, -1.42, -78.45, -1.08, 4326)));

-- Simplified (about 30 m) for the browser; the database checks against the full polygon.
create or replace view public_city_boundaries with (security_invoker = false) as
select c.id, c.name,
       st_asgeojson(st_simplifypreservetopology(c.boundary::geometry, 0.0003), 6)::jsonb as boundary,
       jsonb_build_array(st_xmin(c.boundary::geometry), st_ymin(c.boundary::geometry),
                         st_xmax(c.boundary::geometry), st_ymax(c.boundary::geometry)) as bbox
  from cities c
 where c.is_enabled and c.boundary is not null;
grant select on public_city_boundaries to anon, authenticated;

-- Replaces a city's boundary with a GeoJSON Polygon or MultiPolygon. Refuses a shape that does not
-- contain the city's existing reports' area centre, so a wrong pick (another Ambato) fails loudly.
create or replace function set_city_boundary(p_city text, p_geojson jsonb, p_must_contain jsonb default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  g geometry;
  p geometry;
begin
  if not exists (select 1 from cities where id = p_city) then
    raise exception 'city_not_found' using errcode = 'PT404';
  end if;
  begin
    g := st_makevalid(st_setsrid(st_geomfromgeojson(p_geojson::text), 4326));
  exception when others then
    raise exception 'bad_boundary' using errcode = 'PT422';
  end;
  g := st_multi(st_collectionextract(g, 3));
  if g is null or st_isempty(g) then
    raise exception 'bad_boundary' using errcode = 'PT422';
  end if;
  if p_must_contain is not null then
    p := st_setsrid(st_makepoint((p_must_contain ->> 'lon')::float8, (p_must_contain ->> 'lat')::float8), 4326);
    if not st_covers(g, p) then
      raise exception 'boundary_misses_city' using errcode = 'PT422';
    end if;
  end if;
  update cities set boundary = g::geography where id = p_city;
  return jsonb_build_object('city', p_city, 'area_km2', round((st_area(g::geography) / 1e6)::numeric, 1),
                            'points', st_npoints(g),
                            'bbox', jsonb_build_array(st_xmin(g), st_ymin(g), st_xmax(g), st_ymax(g)),
                            'reports_outside', (select count(*) from reports r
                                                 where r.city_id = p_city and not r.is_sandbox
                                                   and not st_covers(g::geography, r.geom)));
end $$;
revoke all on function set_city_boundary(text, jsonb, jsonb) from public, anon, authenticated;
grant execute on function set_city_boundary(text, jsonb, jsonb) to service_role;
