-- Impossible travel (plan §5.5, "GPS falsificado"): when a device's new report is too far from
-- its previous one for the time between them, a trigger opens an impossible_gps moderation flag.
-- Faster than 150 km/h over more than 2 km counts (GPS jitter and a bus ride do not). The
-- report stays visible until a moderator decides; the orchestrator gives it consistency 0, so
-- it adds no support meanwhile. Sandbox reports have hand-placed pins and are not checked.

set search_path = public, extensions;

insert into app_config (key, value) values ('gps_rules', '{"max_speed_kmh": 150, "min_distance_m": 2000}')
on conflict (key) do nothing;

create function flag_impossible_travel() returns trigger language plpgsql security definer
set search_path = public, extensions as $$
declare
  rules jsonb := coalesce((select value from app_config where key = 'gps_rules'), '{}');
  prev  reports;
  v_m   float8;
  v_h   float8;
begin
  if new.is_sandbox or new.gps_geom is null then
    return null;
  end if;
  select * into prev from reports p
   where p.device_id = new.device_id and p.id <> new.id and p.gps_geom is not null and not p.is_sandbox
     and p.created_at <= new.created_at
   order by p.created_at desc limit 1;
  if not found then
    return null;
  end if;
  v_m := st_distance(prev.gps_geom, new.gps_geom);
  v_h := greatest(extract(epoch from new.created_at - prev.created_at) / 3600, 1.0 / 3600);  -- at least 1 s
  if v_m > coalesce((rules ->> 'min_distance_m')::float8, 2000)
     and v_m / 1000 / v_h > coalesce((rules ->> 'max_speed_kmh')::float8, 150) then
    insert into moderation_flags (city_id, report_id, reason, source, evidence)
    values (new.city_id, new.id, 'impossible_gps', 'trigger', jsonb_build_object(
      'previous_report_id', prev.id, 'distance_m', round(v_m), 'minutes', round((v_h * 60)::numeric, 1),
      'speed_kmh', round((v_m / 1000 / v_h)::numeric)));
  end if;
  return null;
end $$;

create trigger reports_impossible_travel after insert on reports
  for each row execute function flag_impossible_travel();
