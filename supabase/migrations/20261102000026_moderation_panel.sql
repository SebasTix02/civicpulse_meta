-- Minimal moderation panel (/panel/admin, API contract v0.2 §10): the queue of flagged and hidden
-- reports, and the category catalog's status. Until now both were done by hand in Supabase Studio.
--
-- Who may do what comes from user_roles, read directly like member_can (migration 14), so it does
-- not depend on the custom access token hook:
--   reports.moderate     city_admin of the city, or super_admin   -> the queue and its actions
--   categories.activate  catalog_editor or super_admin (global)   -> activate / retire categories
-- Every action is written to audit_log. Moderators see the redacted text and the public
-- (anonymized) photo only, never the raw text or the raw photo.

set search_path = public, extensions;

-- Does the signed-in staff member hold `perm` (a global role, or a city role for p_city)?
create or replace function staff_can(perm text, p_city text default null) returns boolean
language sql stable security definer set search_path = public as $$
  select not coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) and exists (
    select 1
      from user_roles ur
      join roles ro on ro.id = ur.role_id
      join role_permissions rp on rp.role_id = ur.role_id and rp.permission_id = perm
      join profiles p on p.user_id = ur.user_id and p.is_active
     where ur.user_id = auth.uid() and ur.revoked_at is null
       and (ro.scope = 'global' or (ro.scope = 'city' and p_city is not null and ur.city_id = p_city)))
$$;
grant execute on function staff_can(text, text) to authenticated;

-- What the admin panel shows to the caller (and whether to link it from the actors' panel).
create or replace function admin_access(p_city text default 'ambato') returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('city_id', p_city,
                            'moderate', staff_can('reports.moderate', p_city),
                            'catalog', staff_can('categories.activate', p_city),
                            'audit', staff_can('audit.read', p_city));
$$;
grant execute on function admin_access(text) to authenticated;

create or replace function admin_audit(p_city text, p_action text, p_entity text, p_entity_id text,
                                       p_before jsonb, p_after jsonb) returns void
language sql volatile security definer set search_path = public as $$
  insert into audit_log (actor_user, city_id, action, entity, entity_id, before, after)
  values ((select user_id from profiles where user_id = auth.uid()), p_city, p_action, p_entity, p_entity_id, p_before, p_after);
$$;
revoke all on function admin_audit(text, text, text, text, jsonb, jsonb) from public, anon, authenticated;

-- ---------------------------------------------------------------- the queue

-- p_view: 'flagged' (open flags), 'hidden', or 'recent' (latest visible reports, to hide by hand).
create or replace function moderation_queue(p_city text default 'ambato', p_view text default 'flagged',
                                            p_limit int default 50) returns jsonb
language plpgsql stable security definer set search_path = public, extensions as $$
begin
  if not staff_can('reports.moderate', p_city) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  if p_view not in ('flagged', 'hidden', 'recent') then
    raise exception 'invalid_view' using errcode = 'PT422';
  end if;
  return coalesce((
    select jsonb_agg(x order by x.sort_at desc) from (
      select r.id, 'r_' || left(r.id::text, 8) as short_ref, r.created_at, r.category_id,
             cat.display_name as category_name, r.spec ->> 'summary_en' as summary_en, r.raw_text_redacted,
             r.lang, r.severity, r.intake_status, r.moderation_status, r.moderation_reason, r.moderated_at,
             r.cluster_id, k.status as cluster_status, k.report_count as cluster_reports,
             d.trust as device_trust,
             (select count(*) from reports o where o.device_id = r.device_id
                and o.created_at > now() - interval '1 day') as device_reports_24h,
             (select p.public_path from report_photos l join photos p on p.id = l.photo_id
               where l.report_id = r.id and l.version = r.current_photo_version) as photo_public_path,
             coalesce((select jsonb_agg(jsonb_build_object('id', f.id, 'reason', f.reason, 'evidence', f.evidence,
                                                           'created_at', f.created_at) order by f.created_at)
                         from moderation_flags f where f.report_id = r.id and f.resolved_at is null), '[]') as flags,
             coalesce((select max(f.created_at) from moderation_flags f
                        where f.report_id = r.id and f.resolved_at is null), r.moderated_at, r.created_at) as sort_at
        from reports r
        join devices d on d.id = r.device_id
        left join categories cat on cat.id = r.category_id
        left join clusters k on k.id = r.cluster_id
       where r.city_id = p_city and not r.is_sandbox
         and case p_view
               when 'flagged' then exists (select 1 from moderation_flags f where f.report_id = r.id and f.resolved_at is null)
               when 'hidden' then r.moderation_status = 'hidden'
               else r.moderation_status = 'visible' and r.created_at > now() - interval '30 days' end
       order by sort_at desc
       limit least(greatest(p_limit, 1), 200)) x), '[]'::jsonb);
end $$;
revoke all on function moderation_queue(text, text, int) from public, anon;
grant execute on function moderation_queue(text, text, int) to authenticated;

-- Counts and status of a cluster after one of its reports was hidden or restored. Same support
-- formula as assign_cluster (migration 8). A validated case never goes back to candidate; a case
-- with no visible report left is hidden from the public map, and shown again when one comes back.
create or replace function refresh_cluster_after_moderation(p_cluster uuid) returns clusters
language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  rules jsonb := coalesce((select value from app_config where key = 'cluster_rules'), '{}');
  c     clusters;
  s     record;
begin
  select * into c from clusters where id = p_cluster for update;
  if c.id is null then
    return null;
  end if;
  with members as (
    select rp.device_id, rp.ip_prefix, rp.created_at, coalesce(rp.consistency_score, 0.5) as consistency, d.trust
      from reports rp join devices d on d.id = rp.device_id
     where rp.cluster_id = c.id and rp.moderation_status = 'visible'
  ), per_device as (
    select device_id, max(ip_prefix) as ip_prefix, max(trust * consistency) as score from members group by device_id
  ), weighted as (
    select score / count(*) over (partition by coalesce(ip_prefix, device_id::text)) as w from per_device
  )
  select (select count(*) from members) as n, (select count(*) from per_device) as devices,
         (select coalesce(sum(w), 0) from weighted) as support, (select max(created_at) from members) as last_at
    into s;

  update clusters set
    report_count      = s.n,
    distinct_devices  = s.devices,
    weighted_support  = round(s.support::numeric, 3),
    last_report_at    = coalesce(s.last_at, last_report_at),
    moderation_status = case when s.n = 0 then 'hidden' else 'visible' end
  where id = c.id
  returning * into c;

  if c.status = 'candidate' and c.moderation_status = 'visible'
     and s.devices >= coalesce((rules ->> 'min_devices')::int, 3)
     and s.support >= coalesce((rules ->> 'min_support')::real, 1.2) then
    update clusters set status = 'validated', validated_at = coalesce(validated_at, now()) where id = c.id returning * into c;
    insert into cluster_events (cluster_id, from_status, to_status, actor) values (c.id, 'candidate', 'validated', 'moderation');
  end if;
  return c;
end $$;
revoke all on function refresh_cluster_after_moderation(uuid) from public, anon, authenticated;

-- hide: out of the public map and of consensus (needs a reason); restore: back again;
-- keep: the flags were false alarms, the report stays as it is. Open flags are closed either way.
create or replace function moderate_report(p_report_id uuid, p_action text, p_reason text default null,
                                           p_note text default null) returns jsonb
language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  r      reports;
  c      clusters;
  v_flag text;
  n      int;
begin
  select * into r from reports where id = p_report_id and not is_sandbox for update;
  if r.id is null then
    raise exception 'report_not_found' using errcode = 'PT404';
  end if;
  if not staff_can('reports.moderate', r.city_id) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  if p_action not in ('hide', 'restore', 'keep') then
    raise exception 'invalid_action' using errcode = 'PT422';
  end if;
  if p_action = 'hide' and (p_reason is null or p_reason not in
      ('spam', 'reused_photo', 'personal_data', 'inappropriate', 'impossible_gps', 'duplicate', 'other')) then
    raise exception 'reason_required' using errcode = 'PT422';
  end if;
  if p_action = 'hide' and r.moderation_status = 'hidden' or p_action = 'restore' and r.moderation_status = 'visible' then
    raise exception 'invalid_transition' using errcode = 'PT409';
  end if;

  if p_action = 'hide' then
    update reports set moderation_status = 'hidden', moderation_reason = p_reason,
                       moderated_by = (select user_id from profiles where user_id = auth.uid()), moderated_at = now()
     where id = r.id;
  elsif p_action = 'restore' then
    update reports set moderation_status = 'visible', moderation_reason = null,
                       moderated_by = (select user_id from profiles where user_id = auth.uid()), moderated_at = now()
     where id = r.id;
  end if;
  v_flag := case p_action when 'hide' then 'hidden' when 'restore' then 'restored' else 'kept' end;
  update moderation_flags set resolved_at = now(), resolution = v_flag,
                              resolved_by = (select user_id from profiles where user_id = auth.uid())
   where report_id = r.id and resolved_at is null;
  get diagnostics n = row_count;

  if r.cluster_id is not null and p_action <> 'keep' then
    c := refresh_cluster_after_moderation(r.cluster_id);
  end if;
  perform admin_audit(r.city_id, 'report.' || p_action, 'reports', r.id::text,
                      jsonb_build_object('moderation_status', r.moderation_status, 'moderation_reason', r.moderation_reason),
                      jsonb_build_object('reason', p_reason, 'note', left(p_note, 500), 'flags_closed', n));
  return jsonb_build_object('report_id', r.id, 'action', p_action, 'flags_closed', n,
                            'cluster_id', c.id, 'cluster_status', c.status, 'cluster_visible', c.moderation_status = 'visible',
                            'cluster_reports', c.report_count);
end $$;
revoke all on function moderate_report(uuid, text, text, text) from public, anon;
grant execute on function moderate_report(uuid, text, text, text) to authenticated;

-- ---------------------------------------------------------------- the catalog

create or replace function admin_catalog(p_city text default 'ambato') returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not (staff_can('categories.activate', p_city) or staff_can('reports.moderate', p_city)) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  return coalesce((
    select jsonb_agg(x order by x.status = 'active' desc, x.family, x.id) from (
      select c.id, c.family, c.status, c.display_name, c.photo_verifiability, c.default_priority, c.updated_at,
             coalesce((select jsonb_agg(dc.domain_id order by dc.domain_id) from domain_categories dc
                        where dc.category_id = c.id), '[]') as domains,
             (select count(*) from reports r where r.category_id = c.id and r.city_id = p_city and not r.is_sandbox
                and r.created_at > now() - interval '30 days') as reports_30d,
             (select count(*) from clusters k where k.category_id = c.id and k.city_id = p_city and not k.is_sandbox
                and k.status in ('candidate', 'validated', 'acknowledged', 'resolution_pending')) as open_cases
        from categories c) x), '[]'::jsonb);
end $$;
revoke all on function admin_catalog(text) from public, anon;
grant execute on function admin_catalog(text) to authenticated;

-- draft -> active, active -> retired, retired -> active. A retired category is no longer offered
-- to the spec model nor matched by keywords (the orchestrator reads statuses every minute); its
-- existing reports and cases stay as they are. "other" catches everything and is never retired.
create or replace function set_category_status(p_category text, p_status text, p_note text default null)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  c categories;
begin
  if not staff_can('categories.activate') then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  select * into c from categories where id = p_category for update;
  if c.id is null then
    raise exception 'category_not_found' using errcode = 'PT404';
  end if;
  if p_status not in ('active', 'retired') or c.status = p_status then
    raise exception 'invalid_transition' using errcode = 'PT409';
  end if;
  if p_category = 'other' then
    raise exception 'category_required' using errcode = 'PT409';
  end if;
  update categories set status = p_status, updated_at = now() where id = c.id;
  perform admin_audit(null, 'category.' || case p_status when 'active' then 'activate' else 'retire' end,
                      'categories', c.id, jsonb_build_object('status', c.status),
                      jsonb_build_object('status', p_status, 'note', left(p_note, 500)));
  return jsonb_build_object('category_id', c.id, 'from', c.status, 'to', p_status);
end $$;
revoke all on function set_category_status(text, text, text) from public, anon;
grant execute on function set_category_status(text, text, text) to authenticated;

-- The last moderation and catalog actions, for the panel's history.
create or replace function admin_history(p_city text default 'ambato', p_limit int default 30) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not (staff_can('reports.moderate', p_city) or staff_can('categories.activate', p_city)) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  return coalesce((
    select jsonb_agg(x order by x.at desc) from (
      select a.at, a.action, a.entity_id, a.after, p.display_name as actor
        from audit_log a left join profiles p on p.user_id = a.actor_user
       where (a.action like 'report.%' and a.city_id = p_city) or a.action like 'category.%'
       order by a.at desc limit least(greatest(p_limit, 1), 100)) x), '[]'::jsonb);
end $$;
revoke all on function admin_history(text, int) from public, anon;
grant execute on function admin_history(text, int) to authenticated;
