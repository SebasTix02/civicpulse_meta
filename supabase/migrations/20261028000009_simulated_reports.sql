-- Simulated devices (plan §3.7, E-R §5): consensus in the sandbox, the spammer demo and the
-- simulator (scripts/simulate.py). Only inside a sandbox session, so a simulated device can never
-- support a real cluster: the composite (id, is_sandbox) foreign keys already forbid it, and
-- this function refuses anything else. Only the orchestrator (service_role) calls it.
--
-- simulate_reports() adds `p_devices` simulated devices, each sending `p_reports_each` reports
-- scattered within `p_spread_m` of (lat, lon), as already photo-checked reports (vision_done with
-- the given consistency), and clusters each one with assign_cluster(). `p_network` puts every
-- device on one network prefix (one Wi-Fi); null gives each device its own.

set search_path = public, extensions;

create function simulate_reports(
  p_session      uuid,
  p_category     text,
  p_lat          double precision,
  p_lon          double precision,
  p_devices      int default 3,
  p_reports_each int default 1,
  p_network      text default null,
  p_consistency  real default 1.0,
  p_spread_m     real default 10,
  p_trust        real default 0.5
) returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  s        sandbox_sessions;
  v_domain text;
  v_dev    uuid;
  v_id     uuid;
  v_last   jsonb;
  v_ids    uuid[] := '{}';
begin
  select * into s from sandbox_sessions where id = p_session;
  if not found or s.expires_at < now() then
    raise exception 'sandbox_session_not_found' using errcode = 'PT404';
  end if;
  if p_devices not between 1 and 20 or p_reports_each not between 1 and 10 then
    raise exception 'simulation_too_large' using errcode = 'PT422';
  end if;
  select domain_id into v_domain from domain_categories where category_id = p_category limit 1;
  if v_domain is null then
    raise exception 'unknown_category' using errcode = 'PT422';
  end if;

  for d in 1 .. p_devices loop
    insert into devices (id, is_sandbox, sandbox_session_id, is_simulated, trust)
    values (gen_random_uuid(), true, s.id, true, p_trust) returning id into v_dev;
    for k in 1 .. p_reports_each loop
      -- Random point within p_spread_m (1 degree of latitude is ~111 km).
      insert into reports (id, is_sandbox, sandbox_session_id, device_id, city_id, geom, ip_prefix, status,
                           domain_id, category_id, catalog_version, severity, consistency_score, intake_status,
                           raw_text, lang, spec)
      values (gen_random_uuid(), true, s.id, v_dev, s.city_id,
              st_setsrid(st_makepoint(p_lon + (random() - 0.5) * 2 * p_spread_m / (111320 * cos(radians(p_lat))),
                                      p_lat + (random() - 0.5) * 2 * p_spread_m / 111320), 4326)::geography,
              coalesce(p_network, 'sim-' || left(v_dev::text, 8)), 'vision_done', v_domain, p_category, 1, 3,
              p_consistency,
              case when p_consistency >= 1 then 'REPORT_CONFIRMED_BY_PHOTO'
                   when p_consistency >= 0.7 then 'CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW'
                   else 'NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO' end,
              '', 'en', jsonb_build_object('category_id', p_category, 'summary_en', 'Simulated report', 'source', 'simulator'))
      returning id into v_id;
      v_last := assign_cluster(v_id);
      v_ids := v_ids || v_id;
    end loop;
  end loop;

  return jsonb_build_object('reports', to_jsonb(v_ids), 'last', v_last);
end $$;

revoke all on function simulate_reports(uuid, text, double precision, double precision, int, int, text, real, real, real)
  from public, anon, authenticated;
grant execute on function simulate_reports(uuid, text, double precision, double precision, int, int, text, real, real, real)
  to service_role;
