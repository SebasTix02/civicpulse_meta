-- Replay package (riesgos §4.3): everything one real run of a cluster left in the database, for
-- scripts/record_replay.py. Service role only. Never device ids, networks, raw text or raw photos:
-- devices become 1, 2, 3 in order of their first report, text is the redacted copy, photos are the
-- anonymized public copies. Hidden reports keep their moderation reason but lose text and photo.

set search_path = public, extensions;

create function replay_export(p_cluster_id uuid) returns jsonb
language sql stable security definer set search_path = public, extensions as $$
  with c as (
    select * from clusters where id = p_cluster_id
  ), r as (
    select r.*, d.is_simulated,
           dense_rank() over (order by first_seen.at, r.device_id) as device_no
      from reports r
      join devices d on d.id = r.device_id
      join lateral (select min(created_at) as at from reports x
                     where x.device_id = r.device_id and x.cluster_id = p_cluster_id) first_seen on true
     where r.cluster_id = p_cluster_id
  )
  select jsonb_build_object(
    'cluster', (select jsonb_build_object(
        'id', c.id, 'is_sandbox', c.is_sandbox, 'city_id', c.city_id, 'domain_id', c.domain_id,
        'category_id', c.category_id, 'category_name', cat.display_name,
        'lat', round(st_y(c.centroid::geometry)::numeric, 4), 'lon', round(st_x(c.centroid::geometry)::numeric, 4),
        'status', c.status, 'report_count', c.report_count, 'distinct_devices', c.distinct_devices,
        'weighted_support', c.weighted_support, 'opened_at', c.opened_at, 'validated_at', c.validated_at,
        'resolved_at', c.resolved_at)
      from c join categories cat on cat.id = c.category_id),
    'events', coalesce((select jsonb_agg(jsonb_build_object('at', e.at, 'from', e.from_status, 'to', e.to_status)
                                         order by e.at)
                          from cluster_events e where e.cluster_id = p_cluster_id), '[]'),
    'reports', coalesce((select jsonb_agg(jsonb_build_object(
        'ref', 'r_' || left(r.id::text, 8), 'created_at', r.created_at, 'device_no', r.device_no,
        'simulated', r.is_simulated, 'lang', r.lang, 'status', r.status, 'severity', r.severity,
        'moderation_status', r.moderation_status, 'moderation_reason', r.moderation_reason,
        'text', case when r.moderation_status = 'visible' then r.raw_text_redacted end,
        'summary_en', r.spec ->> 'summary_en', 'category_id', r.category_id,
        'intake_status', r.intake_status, 'consistency_score', r.consistency_score,
        'intake', case when r.moderation_status = 'visible' then r.intake end,
        'photo_public_path', case when r.moderation_status = 'visible' then p.public_path end,
        'stages', coalesce((select jsonb_agg(jsonb_build_object(
                     'stage', s.stage, 'status', s.status, 'provider', s.provider, 'model', s.model,
                     'latency_ms', s.latency_ms, 'started_at', s.started_at) order by s.started_at)
                     from stage_runs s where s.report_id = r.id), '[]'))
        order by r.created_at) from r
        left join report_photos rp on rp.report_id = r.id and rp.version = r.current_photo_version
        left join photos p on p.id = rp.photo_id), '[]'),
    'dispatches', coalesce((select jsonb_agg(jsonb_build_object(
        'stakeholder_name', s.name, 'status', d.status, 'created_at', d.created_at, 'seen_at', d.seen_at,
        'note', d.note, 'brief', d.brief,
        'brief_stage', (select jsonb_build_object('model', sr.model, 'latency_ms', sr.latency_ms, 'started_at', sr.started_at)
                          from stage_runs sr where sr.cluster_id = d.cluster_id and sr.stakeholder_id = d.stakeholder_id
                           and sr.stage = 'brief' and sr.status = 'ok' order by sr.started_at desc limit 1))
        order by d.created_at)
        from dispatches d join stakeholders s on s.id = d.stakeholder_id
       where d.cluster_id = p_cluster_id and d.brief is not null), '[]'),
    'resolutions', coalesce((select jsonb_agg(jsonb_build_object(
        'id', x.id, 'closure_path', x.closure_path, 'created_at', x.created_at, 'ai_status', x.ai_status,
        'outcome', x.outcome, 'ai_overridden', x.ai_overridden, 'decided_at', x.decided_at,
        'verification', x.verification, 'after_public_path', p.public_path,
        'stages', coalesce((select jsonb_agg(jsonb_build_object(
                     'stage', s.stage, 'status', s.status, 'provider', s.provider, 'model', s.model,
                     'latency_ms', s.latency_ms, 'started_at', s.started_at) order by s.started_at)
                     from stage_runs s where s.resolution_id = x.id), '[]'),
        'reviews', coalesce((select jsonb_agg(jsonb_build_object('verdict', v.verdict, 'note', v.note, 'at', v.created_at)
                                              order by v.created_at)
                               from resolution_reviews v where v.resolution_id = x.id), '[]'))
        order by x.created_at)
        from resolutions x join photos p on p.id = x.photo_id where x.cluster_id = p_cluster_id), '[]'))
  where exists (select 1 from c)
$$;
revoke all on function replay_export(uuid) from public, anon, authenticated;
grant execute on function replay_export(uuid) to service_role;
