-- Smoke test for the city boundary (migration 25).
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

do $$
declare
  x jsonb;
  pelileo constant geography := st_setsrid(st_makepoint(-78.5436, -1.3297), 4326)::geography;
  centro  constant geography := st_setsrid(st_makepoint(-78.6197, -1.2417), 4326)::geography;
begin
  assert (select st_covers(boundary, centro) and not st_covers(boundary, pelileo) from cities where id = 'ambato'),
    'the box keeps Ambato and leaves Pelileo out';
  x := (select to_jsonb(b) from public_city_boundaries b where id = 'ambato');
  assert x -> 'boundary' ->> 'type' in ('Polygon', 'MultiPolygon') and jsonb_array_length(x -> 'bbox') = 4, x::text;

  begin
    perform set_city_boundary('ambato', '{"type": "Point", "coordinates": [1, 2]}');
    assert false, 'a point is not a boundary';
  exception when others then assert sqlerrm = 'bad_boundary', sqlerrm;
  end;
  begin
    perform set_city_boundary('ambato', '{"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[0,0]]]}',
                              '{"lat": -1.2417, "lon": -78.6197}');
    assert false, 'another place';
  exception when others then assert sqlerrm = 'boundary_misses_city', sqlerrm;
  end;

  -- A triangle around the centre that leaves the east out.
  x := set_city_boundary('ambato', '{"type": "Polygon", "coordinates": [[[-78.75,-1.15],[-78.58,-1.24],[-78.75,-1.35],[-78.75,-1.15]]]}',
                         '{"lat": -1.2417, "lon": -78.6197}');
  assert (x ->> 'area_km2')::numeric > 100 and x ->> 'points' = '4', x::text;
  assert (select st_covers(boundary, centro) and not st_covers(boundary, st_setsrid(st_makepoint(-78.57, -1.24), 4326)::geography)
            from cities where id = 'ambato');
end $$;
rollback;
\echo 'smoke_city_boundary: ok'
