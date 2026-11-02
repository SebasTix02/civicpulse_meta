-- Sandbox, two ways to reach consensus, and only inside the pilot city.
--
-- 1. Sandbox reports must fall inside the session city's boundary, like real ones
--    (outside_service_area). Before, a sandbox pin could go anywhere in the world.
-- 2. Besides the quick path (sandbox_simulate_neighbours: three good neighbours at once), the
--    visitor can now place each simulated neighbour by hand with sandbox_add_neighbour, and pick
--    what kind of neighbour it is:
--      neighbour      a phone of its own, on its own network, photo shows the problem
--      same_network   a phone on the visitor's network: counts as a device, but shares support
--      unclear_photo  its own phone and network, but the photo does not show the problem
--    A neighbour farther than the cluster radius opens a case of its own, as on the real map.
--    That shows the consensus rules working instead of a scripted happy path.
-- 3. sandbox_state returns the pin of each report, its hand-placed neighbours and the rules.

set search_path = public, extensions;

update app_config set value = value || '{"manual_neighbours": 6}'
 where key = 'sandbox_limits' and not value ? 'manual_neighbours';

create or replace function sandbox_manual_neighbour_cap() returns int
language sql stable set search_path = public as $$
  select coalesce((select (value ->> 'manual_neighbours')::int from app_config where key = 'sandbox_limits'), 6);
$$;

-- 1. The pilot city's boundary also holds in the sandbox.
create or replace function reports_sandbox_in_city() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  if new.is_sandbox and not exists (select 1 from cities c where c.id = new.city_id and c.boundary is not null
                                                              and st_covers(c.boundary, new.geom)) then
    raise exception 'outside_service_area' using errcode = 'PT422';
  end if;
  return new;
end $$;

create trigger reports_sandbox_in_city before insert on reports
  for each row when (new.is_sandbox) execute function reports_sandbox_in_city();

-- 2. One simulated neighbour at a point the visitor chose.
create or replace function sandbox_add_neighbour(p_report_id uuid, p_lat double precision, p_lon double precision,
                                                  p_kind text default 'neighbour') returns jsonb
language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  dev   devices := sandbox_device();
  r     reports;
  n     reports;
  c     clusters;
  v_out jsonb;
  v_id  uuid;
begin
  select * into r from reports where id = p_report_id and device_id = dev.id for update;
  if r.id is null then
    raise exception 'report_not_found' using errcode = 'PT404';
  end if;
  if r.status <> 'clustered' or r.cluster_id is null then
    raise exception 'report_not_ready' using errcode = 'PT409';
  end if;
  if p_kind not in ('neighbour', 'same_network', 'unclear_photo') then
    raise exception 'invalid_neighbour_kind' using errcode = 'PT422';
  end if;
  if (select count(*) from reports x where x.sandbox_session_id = r.sandbox_session_id
                                       and x.spec ->> 'for_report' = r.id::text) >= sandbox_manual_neighbour_cap() then
    raise exception 'sandbox_neighbours_exceeded' using errcode = 'PT429';
  end if;
  if p_lat is null or p_lon is null or p_lat not between -90 and 90 or p_lon not between -180 and 180 then
    raise exception 'outside_service_area' using errcode = 'PT422';
  end if;

  -- A neighbour on the visitor's network must share a prefix with the visitor's report.
  if p_kind = 'same_network' and r.ip_prefix is null then
    update reports set ip_prefix = 'sbx-' || left(dev.id::text, 8) where id = r.id returning * into r;
  end if;
  v_out := simulate_reports(r.sandbox_session_id, r.category_id, p_lat, p_lon, 1, 1,
                            case when p_kind = 'same_network' then r.ip_prefix end,
                            case when p_kind = 'unclear_photo' then 0.3 else 1.0 end, 0, 0.6);
  v_id := (v_out -> 'reports' ->> 0)::uuid;
  update reports set spec = spec || jsonb_build_object('for_report', r.id, 'neighbour_kind', p_kind)
   where id = v_id returning * into n;
  select * into c from clusters where id = r.cluster_id;
  return jsonb_build_object('report_id', n.id, 'kind', p_kind, 'joined', n.cluster_id = r.cluster_id,
                            'distance_m', round(st_distance(n.geom, r.geom)::numeric, 0),
                            'cluster_id', c.id, 'status', c.status,
                            'distinct_devices', c.distinct_devices, 'weighted_support', c.weighted_support);
end $$;
revoke all on function sandbox_add_neighbour(uuid, double precision, double precision, text) from public, anon;
grant execute on function sandbox_add_neighbour(uuid, double precision, double precision, text) to authenticated;

-- 3. Same as migration 20, plus rules, each report's pin and its hand-placed neighbours.
create or replace function sandbox_state() returns jsonb
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  dev devices;
  s   sandbox_sessions;
begin
  begin
    dev := sandbox_device();
  exception when sqlstate 'PT404' then
    return jsonb_build_object('session', null);
  end;
  select * into s from sandbox_sessions where id = dev.sandbox_session_id;

  return jsonb_build_object(
    'session', jsonb_build_object('id', s.id, 'city_id', s.city_id, 'expires_at', s.expires_at),
    'rules', (select jsonb_build_object('radius_m', coalesce(v.value -> 'radius_m', '{"default": 40}'::jsonb),
                                        'min_devices', coalesce((v.value ->> 'min_devices')::int, 3),
                                        'min_support', coalesce((v.value ->> 'min_support')::real, 1.2),
                                        'manual_neighbours', sandbox_manual_neighbour_cap())
                from (select (select value from app_config where key = 'cluster_rules') as value) v),
    'quota', sandbox_quota(dev.id),
    'reports', coalesce((
      select jsonb_agg(x order by x.created_at desc) from (
        select r.id, r.created_at, r.status, r.intake_status, r.category_id, cat.display_name as category_name,
               r.cluster_id, k.status as cluster_status, p.public_path as photo_public_path, smp.id as sample_id,
               r.raw_text, st_y(r.geom::geometry) as lat, st_x(r.geom::geometry) as lon,
               coalesce((select jsonb_agg(jsonb_build_object('id', n.id, 'lat', st_y(n.geom::geometry),
                                                            'lon', st_x(n.geom::geometry), 'kind', n.spec ->> 'neighbour_kind',
                                                            'cluster_id', n.cluster_id,
                                                            'distance_m', round(st_distance(n.geom, r.geom)::numeric, 0))
                                          order by n.created_at)
                           from reports n
                          where n.sandbox_session_id = s.id and n.spec ->> 'for_report' = r.id::text), '[]'::jsonb) as neighbours
          from reports r
          join report_photos l on l.report_id = r.id and l.version = r.current_photo_version
          join photos p on p.id = l.photo_id
          left join sandbox_samples smp on smp.before_path = p.storage_path
          left join categories cat on cat.id = r.category_id
          left join clusters k on k.id = r.cluster_id
         where r.device_id = dev.id and r.sandbox_session_id = s.id
         order by r.created_at desc limit 20) x), '[]'::jsonb),
    'cases', coalesce((
      select jsonb_agg(x order by x.opened_at desc) from (
        select c.id, c.status, c.domain_id, c.category_id, cat.display_name as category_name, c.opened_at,
               c.report_count, c.distinct_devices, c.weighted_support,
               st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lon,
               (select count(*) from reports rr join devices dd on dd.id = rr.device_id
                 where rr.cluster_id = c.id and dd.is_simulated) as simulated_reports,
               (select jsonb_build_object('stakeholder_id', d.stakeholder_id, 'stakeholder_name', st.name,
                                          'status', d.status, 'brief', d.brief, 'created_at', d.created_at)
                  from dispatches d join stakeholders st on st.id = d.stakeholder_id
                 where d.cluster_id = c.id order by d.created_at limit 1) as dispatch,
               (select jsonb_build_object(
                         'id', z.id, 'ai_status', z.ai_status, 'outcome', z.outcome, 'ai_overridden', z.ai_overridden,
                         'created_at', z.created_at, 'decided_at', z.decided_at, 'verification', z.verification,
                         'after_public_path', ap.public_path,
                         'before_public_path', (select bp.public_path from report_photos bl join photos bp on bp.id = bl.photo_id
                                                 join reports br on br.id = bl.report_id
                                                 where bl.report_id = z.before_report_id
                                                   and bl.version = br.current_photo_version),
                         'review_note', (select rv.note from resolution_reviews rv
                                          where rv.resolution_id = z.id order by rv.created_at desc limit 1))
                  from resolutions z join photos ap on ap.id = z.photo_id
                 where z.cluster_id = c.id order by z.created_at desc limit 1) as resolution
          from clusters c
          join categories cat on cat.id = c.category_id
         where c.sandbox_session_id = s.id
           and exists (select 1 from reports o where o.cluster_id = c.id and o.device_id = dev.id)
         order by c.opened_at desc limit 20) x), '[]'::jsonb));
end $$;
revoke all on function sandbox_state() from public, anon;
grant execute on function sandbox_state() to authenticated;
