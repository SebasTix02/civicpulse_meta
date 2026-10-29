-- Briefs (plan §3.3 step 8, contracts/schemas/brief.schema.json): deterministic SQL builds the
-- context, one Muse call writes the text, a validator checks every citation. brief_context()
-- is that SQL: everything a brief may say about one dispatch, each fact with the id the text
-- must cite. Hidden reports never appear, and neither does citizen text (no raw_text, no
-- summary_en): only counts, dates, severities and the photo check.
--
-- Citation ids: r_<first 8 hex of the report id>, c_<first 8 hex of the cluster id>,
-- geo_features.source_ref (osm_..., naufest_...) and m_<metric>.
--
-- stage_runs also records the brief stage, which belongs to a dispatch, not to a report.

set search_path = public, extensions;

alter table stage_runs add column cluster_id uuid references clusters (id) on delete cascade,
                       add column stakeholder_id uuid references stakeholders (id) on delete cascade;
alter table stage_runs drop constraint stage_runs_check;
alter table stage_runs add constraint stage_runs_subject_check check (
  num_nonnulls(report_id, resolution_id, cluster_id) = 1 and (cluster_id is null) = (stakeholder_id is null));
create index stage_runs_dispatch_idx on stage_runs (cluster_id, stakeholder_id, stage);

create function brief_context(p_cluster uuid, p_stakeholder uuid) returns jsonb
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  c   clusters;
  s   stakeholders;
  cat categories;
  dom domains;
  v_reports jsonb;
  v_nearby  jsonb;
  v_metrics jsonb;
begin
  select * into c from clusters where id = p_cluster;
  select * into s from stakeholders where id = p_stakeholder;
  if c.id is null or s.id is null
     or not exists (select 1 from dispatches where cluster_id = p_cluster and stakeholder_id = p_stakeholder) then
    raise exception 'dispatch_not_found' using errcode = 'PT404';
  end if;
  select * into cat from categories where id = c.category_id;
  select * into dom from domains where id = c.domain_id;

  select coalesce(jsonb_agg(r order by r ->> 'created_at' desc), '[]') into v_reports
    from (select jsonb_build_object(
                   'ref', 'r_' || left(id::text, 8), 'id', id,
                   'created_at', to_char(created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
                   'severity', severity, 'intake_status', intake_status,
                   'photo_confirmed', intake_status = 'REPORT_CONFIRMED_BY_PHOTO',
                   'people_count', (intake ->> 'people_count')::int) as r
            from reports
           where cluster_id = c.id and moderation_status = 'visible'
           order by created_at desc limit 30) x;

  select coalesce(jsonb_agg(f order by (f ->> 'distance_m')::int), '[]') into v_nearby
    from (select jsonb_build_object('ref', g.source_ref, 'layer', g.layer, 'name', g.name, 'official', g.official,
                                    'distance_m', round(st_distance(g.geom, c.centroid))::int) as f
            from geo_features g
           where g.city_id = c.city_id and st_dwithin(g.geom, c.centroid, 200)
             and g.source_ref ~ '^(osm_[0-9]+|naufest_[A-Za-z0-9_]+)$'
           order by st_distance(g.geom, c.centroid) limit 8) y;

  select jsonb_build_object(
           'm_report_count', count(*),
           'm_distinct_devices', count(distinct device_id),
           'm_photo_confirmed', count(*) filter (where intake_status = 'REPORT_CONFIRMED_BY_PHOTO'),
           'm_max_severity', max(severity),
           'm_people_count', max((intake ->> 'people_count')::int),
           'm_days_open', greatest(0, extract(day from now() - c.opened_at))::int,
           'm_weighted_support', round(c.weighted_support::numeric, 2),
           'm_priority_score', round(c.priority_score::numeric, 2))
    into v_metrics
    from reports where cluster_id = c.id and moderation_status = 'visible';

  return jsonb_build_object(
    'cluster', jsonb_build_object(
      'ref', 'c_' || left(c.id::text, 8), 'id', c.id, 'status', c.status, 'domain_id', c.domain_id,
      'category_id', c.category_id, 'category_name', cat.display_name,
      'issue_description', cat.issue_description, 'resolved_description', cat.resolved_description,
      'opened_at', c.opened_at, 'validated_at', c.validated_at, 'last_report_at', c.last_report_at,
      'dist_to_official_stop_m', c.dist_to_official_stop_m),
    'stakeholder', jsonb_build_object('id', s.id, 'name', s.name, 'kind', s.kind,
                                      'profile', s.brief_profile, 'lang', s.brief_lang),
    'actions', to_jsonb(coalesce(dom.action_catalog, '{}')),
    'metrics', v_metrics,
    'reports', v_reports,
    'nearby', v_nearby);
end $$;

revoke all on function brief_context(uuid, uuid) from public, anon, authenticated;
grant execute on function brief_context(uuid, uuid) to service_role;
