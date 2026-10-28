-- Consensus (plan §3.3 step 5 and §5.5): vision_done -> clustered.
-- assign_cluster() puts one report in the nearest open cluster of the same city, domain,
-- category and sandbox session (or opens one), recomputes the weighted support and promotes the
-- cluster to `validated` when enough distinct devices back it. Incremental nearest-cluster
-- assignment instead of a full ST_ClusterDBSCAN pass: same result at hackathon volumes, and it
-- runs inside the report's own transaction. Only the orchestrator (service_role) calls it.

set search_path = public, extensions;

insert into app_config (key, value) values
  ('cluster_rules', '{"radius_m": {"default": 40, "transit": 80}, "min_devices": 3, "min_support": 1.2,
                      "stop_far_m": 200}')
on conflict (key) do nothing;

create function assign_cluster(p_report_id uuid)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  rules    jsonb := coalesce((select value from app_config where key = 'cluster_rules'), '{}');
  r        reports;
  c        clusters;
  v_radius real;
  v_new    boolean := false;
  v_promoted boolean := false;
  v_stats  record;
  v_status text;
begin
  select * into r from reports where id = p_report_id for update;
  if not found then
    raise exception 'report_not_found' using errcode = 'PT404';
  end if;
  if r.status <> 'vision_done' then
    return jsonb_build_object('report_id', r.id, 'skipped', true, 'status', r.status, 'cluster_id', r.cluster_id);
  end if;
  if r.domain_id is null or r.category_id is null then
    raise exception 'report_without_category' using errcode = 'PT409';
  end if;

  -- One writer per (city, domain, category) at a time, so two reports a second apart cannot
  -- open two clusters for the same problem.
  perform pg_advisory_xact_lock(hashtext(r.city_id || '/' || r.domain_id || '/' || r.category_id));

  v_radius := coalesce((rules -> 'radius_m' ->> r.domain_id)::real, (rules -> 'radius_m' ->> 'default')::real, 40);
  select * into c from clusters k
   where k.city_id = r.city_id and k.domain_id = r.domain_id and k.category_id = r.category_id
     and k.is_sandbox = r.is_sandbox and k.sandbox_session_id is not distinct from r.sandbox_session_id
     and k.status <> 'resolved' and k.moderation_status = 'visible'
     and st_dwithin(k.centroid, r.geom, v_radius)
   order by st_distance(k.centroid, r.geom)
   limit 1
   for update;

  if not found then
    insert into clusters (is_sandbox, sandbox_session_id, city_id, domain_id, category_id, centroid)
    values (r.is_sandbox, r.sandbox_session_id, r.city_id, r.domain_id, r.category_id, r.geom)
    returning * into c;
    insert into cluster_events (cluster_id, from_status, to_status, actor) values (c.id, null, 'candidate', 'orchestrator');
    v_new := true;
  end if;

  update reports set cluster_id = c.id, status = 'clustered' where id = r.id;

  -- Support: each device counts once (its best report), as trust x photo consistency, divided
  -- among the devices that share its network prefix (one Wi-Fi full of phones counts as one).
  with members as (
    select rp.device_id, rp.ip_prefix, rp.geom, rp.severity, rp.created_at,
           coalesce(rp.consistency_score, 0.5) as consistency, d.trust
      from reports rp join devices d on d.id = rp.device_id
     where rp.cluster_id = c.id and rp.moderation_status = 'visible'
  ), per_device as (
    select device_id, max(ip_prefix) as ip_prefix, max(trust * consistency) as score from members group by device_id
  ), weighted as (
    select score / count(*) over (partition by coalesce(ip_prefix, device_id::text)) as w from per_device
  )
  select (select count(*) from members) as report_count,
         (select count(*) from per_device) as distinct_devices,
         (select coalesce(sum(w), 0) from weighted) as support,
         (select avg(severity) from members) as severity,
         (select avg(consistency) from members) as consistency,
         (select max(created_at) from members) as last_at,
         (select st_centroid(st_collect(geom::geometry))::geography from members) as centroid
    into v_stats;

  v_status := c.status;
  if c.status = 'candidate' and v_stats.distinct_devices >= coalesce((rules ->> 'min_devices')::int, 3)
     and v_stats.support >= coalesce((rules ->> 'min_support')::real, 1.2) then
    v_status := 'validated';
    v_promoted := true;
    insert into cluster_events (cluster_id, from_status, to_status, actor) values (c.id, 'candidate', 'validated', 'orchestrator');
  end if;

  -- Priority (plan §6), first weights: devices, support, severity, photo evidence, days open.
  update clusters set
    report_count     = v_stats.report_count,
    distinct_devices = v_stats.distinct_devices,
    weighted_support = round(v_stats.support::numeric, 3),
    centroid         = coalesce(v_stats.centroid, centroid),
    last_report_at   = v_stats.last_at,
    status           = v_status,
    validated_at     = case when v_status = 'validated' and validated_at is null then now() else validated_at end,
    dist_to_official_stop_m = case when c.domain_id = 'transit' then (
      select min(st_distance(g.geom, coalesce(v_stats.centroid, c.centroid))) from geo_features g
       where g.city_id = c.city_id and g.layer = 'transit_stops' and g.official) end,
    priority_score   = round((ln(1 + v_stats.distinct_devices) + v_stats.support + 0.5 * coalesce(v_stats.severity, 3)
                              + coalesce(v_stats.consistency, 0.5)
                              + 0.1 * least(30, extract(epoch from now() - c.opened_at) / 86400))::numeric, 3)
  where id = c.id
  returning * into c;

  return jsonb_build_object('report_id', r.id, 'cluster_id', c.id, 'new_cluster', v_new, 'status', c.status,
                            'validated_now', v_promoted,
                            'report_count', c.report_count, 'distinct_devices', c.distinct_devices,
                            'weighted_support', c.weighted_support, 'priority_score', c.priority_score,
                            'dist_to_official_stop_m', c.dist_to_official_stop_m);
end $$;

revoke all on function assign_cluster(uuid) from public, anon, authenticated;
grant execute on function assign_cluster(uuid) to service_role;
