-- Smoke test for hand-placed sandbox neighbours and the city boundary (migration 24).
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;

insert into sandbox_samples (id, category_id, title, suggested_text, before_path, after_path) values
  ('pothole_pair', 'pothole', '{"es": "Bache", "en": "Pothole"}', '{"es": "Bache grande", "en": "Big pothole"}',
   'public/samples/pothole_pair/before.jpg', 'public/samples/pothole_pair/after.jpg')
on conflict (id) do nothing;

do $$
declare
  a   constant text := '{"sub": "5a000000-0000-4000-8000-0000000000c1", "role": "authenticated", "is_anonymous": true}';
  rep constant uuid := '5d000000-0000-4000-8000-0000000000c1';
  x   jsonb;
  cl  uuid;
begin
  perform set_config('request.jwt.claims', a, true);
  perform start_sandbox_session();

  -- Outside the pilot city: refused, in the sandbox too.
  begin
    perform submit_report(gen_random_uuid(), '{"lat": 40.4168, "lon": -3.7038}', 'en', p_sample_id => 'pothole_pair');
    assert false, 'outside the city';
  exception when others then assert sqlerrm = 'outside_service_area', sqlerrm;
  end;

  perform submit_report(rep, '{"lat": -1.2417, "lon": -78.6197}', 'en', p_sample_id => 'pothole_pair');
  begin
    perform sandbox_add_neighbour(rep, -1.2417, -78.6197);
    assert false, 'not on the map yet';
  exception when sqlstate 'PT409' then null;
  end;
  update reports set status = 'vision_done', domain_id = 'roads', category_id = 'pothole', catalog_version = 1,
                     severity = 3, consistency_score = 1.0, intake_status = 'REPORT_CONFIRMED_BY_PHOTO' where id = rep;
  cl := (assign_cluster(rep) ->> 'cluster_id')::uuid;

  -- A phone on the visitor's Wi-Fi and a photo that shows nothing: three devices, still not enough support.
  x := sandbox_add_neighbour(rep, -1.24172, -78.61972, 'same_network');
  assert x ->> 'joined' = 'true' and x ->> 'distinct_devices' = '2', x::text;
  x := sandbox_add_neighbour(rep, -1.24168, -78.61968, 'unclear_photo');
  assert x ->> 'distinct_devices' = '3' and x ->> 'status' = 'candidate', x::text;

  -- 500 m away: a case of its own, the visitor's case does not move.
  x := sandbox_add_neighbour(rep, -1.2462, -78.6197, 'neighbour');
  assert x ->> 'joined' = 'false' and (x ->> 'distance_m')::int between 450 and 550 and x ->> 'distinct_devices' = '3', x::text;

  -- A real neighbour nearby tips it over.
  x := sandbox_add_neighbour(rep, -1.24175, -78.61965, 'neighbour');
  assert x ->> 'status' = 'validated' and x ->> 'distinct_devices' = '4', x::text;
  assert exists (select 1 from dispatches where cluster_id = cl);
  -- The quick path is no longer offered for this case.
  assert sandbox_simulate_neighbours(rep) ->> 'already' = 'true';

  x := sandbox_state();
  assert jsonb_array_length(x -> 'reports' -> 0 -> 'neighbours') = 4, x::text;
  assert x -> 'reports' -> 0 -> 'neighbours' -> 2 ->> 'kind' = 'neighbour'
     and x -> 'reports' -> 0 -> 'neighbours' -> 2 ->> 'cluster_id' <> cl::text, x::text;
  assert (x -> 'reports' -> 0 ->> 'lat')::float8 between -1.2418 and -1.2416, x::text;
  assert x -> 'rules' ->> 'min_devices' = '3' and x -> 'rules' -> 'radius_m' ->> 'default' = '40', x::text;
  assert x -> 'cases' -> 0 ->> 'domain_id' = 'roads', x::text;

  -- Kind checked, cap per report, never outside the city.
  begin perform sandbox_add_neighbour(rep, -1.2417, -78.6197, 'robot'); assert false;
  exception when others then assert sqlerrm = 'invalid_neighbour_kind', sqlerrm; end;
  begin perform sandbox_add_neighbour(rep, 40.4, -3.7, 'neighbour'); assert false;
  exception when others then assert sqlerrm = 'outside_service_area', sqlerrm; end;
  update app_config set value = value || '{"manual_neighbours": 4}' where key = 'sandbox_limits';
  begin perform sandbox_add_neighbour(rep, -1.2417, -78.6197, 'neighbour'); assert false;
  exception when sqlstate 'PT429' then null; end;
end $$;
rollback;
\echo 'smoke_sandbox_neighbours: ok'
