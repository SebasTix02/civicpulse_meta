-- Permissions in the JWT (custom access token hook), has_permission() and base RLS.
-- Writes from the browser go through security definer RPCs; tables are read-only to
-- clients except where a policy below says otherwise. service_role bypasses RLS.

set search_path = public, extensions;

-- ---------------------------------------------------------------- JWT hook

-- Enable in Supabase: Authentication > Hooks > Custom access token > public.custom_access_token_hook
create function custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  uid     uuid := (event ->> 'user_id')::uuid;
  claims  jsonb := coalesce(event -> 'claims', '{}');
  grants  jsonb;
  active  boolean;
begin
  select p.is_active into active from profiles p where p.user_id = uid;

  select coalesce(jsonb_agg(jsonb_build_object(
           'role', ur.role_id,
           'scope', r.scope,
           'city_id', ur.city_id,
           'stakeholder_id', ur.stakeholder_id,
           'permissions', (select coalesce(jsonb_agg(rp.permission_id order by rp.permission_id), '[]')
                           from role_permissions rp where rp.role_id = ur.role_id))), '[]')
    into grants
    from user_roles ur join roles r on r.id = ur.role_id
   where ur.user_id = uid and ur.revoked_at is null and coalesce(active, false);

  claims := claims || jsonb_build_object('cp_grants', grants, 'cp_profile_active', coalesce(active, false));
  return jsonb_set(event, '{claims}', claims);
end $$;

grant execute on function custom_access_token_hook(jsonb) to supabase_auth_admin;
revoke execute on function custom_access_token_hook(jsonb) from authenticated, anon, public;

-- A global grant matches anything; a city grant matches that city and its stakeholders;
-- a stakeholder grant matches only that stakeholder.
create function has_permission(perm text, p_city_id text default null, p_stakeholder_id uuid default null)
returns boolean language sql stable set search_path = public as $$
  select exists (
    select 1
      from jsonb_array_elements(coalesce(auth.jwt() -> 'cp_grants', '[]')) g
     where coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) = false
       and g -> 'permissions' ? perm
       and (
         g ->> 'scope' = 'global'
         or (g ->> 'scope' = 'city' and (
               g ->> 'city_id' = p_city_id
               or (p_stakeholder_id is not null and exists (
                     select 1 from stakeholders s where s.id = p_stakeholder_id and s.city_id = g ->> 'city_id'))))
         or (g ->> 'scope' = 'stakeholder' and (g ->> 'stakeholder_id')::uuid = p_stakeholder_id)
       )
  )
$$;

create function is_anonymous_session() returns boolean language sql stable as $$
  select coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false)
$$;

-- ---------------------------------------------------------------- seed data that code depends on

insert into roles (id, display_name, scope) values
  ('super_admin',       '{"en": "Super admin", "es": "Superadministrador"}', 'global'),
  ('city_admin',        '{"en": "City admin", "es": "Administrador de ciudad"}', 'city'),
  ('catalog_editor',    '{"en": "Catalog editor", "es": "Editor del catálogo"}', 'global'),
  ('stakeholder_admin', '{"en": "Stakeholder admin", "es": "Administrador del actor"}', 'stakeholder'),
  ('stakeholder_agent', '{"en": "Stakeholder agent", "es": "Agente del actor"}', 'stakeholder');

insert into permissions (id, description) values
  ('dispatches.read',       'Read dispatches and dispatched clusters'),
  ('dispatches.respond',    'Accept or decline a dispatch'),
  ('dispatches.redispatch', 'Send a cluster to another stakeholder'),
  ('resolutions.submit',    'Upload an after photo'),
  ('resolutions.approve',   'Approve a resolution, also against the AI'),
  ('members.manage',        'Manage members of own stakeholder'),
  ('users.manage',          'Invite users and grant or revoke roles'),
  ('categories.write',      'Edit catalog categories'),
  ('categories.activate',   'Activate or retire categories'),
  ('domains.write',         'Edit domain packs'),
  ('stakeholders.manage',   'Create and edit stakeholders'),
  ('reports.moderate',      'Hide or restore reports'),
  ('audit.read',            'Read the audit log');

insert into role_permissions (role_id, permission_id)
select 'super_admin', id from permissions
union all select 'city_admin', unnest(array['stakeholders.manage', 'users.manage', 'reports.moderate', 'dispatches.redispatch', 'audit.read'])
union all select 'catalog_editor', unnest(array['categories.write', 'categories.activate', 'domains.write'])
union all select 'stakeholder_admin', unnest(array['dispatches.read', 'dispatches.respond', 'resolutions.submit', 'resolutions.approve', 'members.manage'])
union all select 'stakeholder_agent', unnest(array['dispatches.read', 'dispatches.respond', 'resolutions.submit']);

insert into app_config (key, value) values
  ('demo_level_override',   '{"level": null}'),
  ('sandbox_daily_cap_usd', '{"usd": 5}'),
  ('sandbox_spend',         '{"date": null, "usd": 0}'),
  ('provider_health',       '{"checked_at": null}'),
  ('replay_bundles',        '{"current": null}');

-- ---------------------------------------------------------------- RLS

do $$
declare t text;
begin
  foreach t in array array[
    'domains', 'profiles', 'categories', 'category_versions', 'domain_categories', 'cities',
    'geo_features', 'roles', 'permissions', 'role_permissions', 'sandbox_sessions', 'stakeholders',
    'user_roles', 'audit_log', 'app_config', 'devices', 'capture_nonces', 'photos', 'clusters',
    'reports', 'report_photos', 'cluster_events', 'stakeholder_coverage', 'dispatches',
    'resolutions', 'resolution_reviews', 'device_trust_events', 'stage_runs', 'moderation_flags']
  loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;

-- Public catalog and geography
create policy read_active_domains on domains for select using (status = 'active');
create policy read_active_categories on categories for select using (status = 'active' or has_permission('categories.write'));
create policy read_domain_categories on domain_categories for select using (true);
create policy read_enabled_cities on cities for select using (is_enabled or sandbox_enabled);
create policy read_geo_features on geo_features for select using (true);
create policy read_roles on roles for select using (true);

-- Devices see their own rows (Realtime on reports needs a select policy)
create policy own_device on devices for select using (id = auth.uid());
create policy own_reports on reports for select using (device_id = auth.uid());

-- Actors
create policy own_profile on profiles for select using (user_id = auth.uid() or has_permission('users.manage'));
create policy own_user_roles on user_roles for select using (user_id = auth.uid() or has_permission('users.manage', city_id, stakeholder_id));
create policy stakeholder_dispatches on dispatches for select using (has_permission('dispatches.read', null, stakeholder_id));
create policy stakeholder_resolutions on resolutions for select using (
  has_permission('dispatches.read', null, stakeholder_id) or submitted_by_device = auth.uid());
create policy moderators_flags on moderation_flags for select using (has_permission('reports.moderate', city_id));
create policy audit_readers on audit_log for select using (has_permission('audit.read', city_id));

-- ---------------------------------------------------------------- public views

-- Only real, visible, validated or resolved clusters; public columns only.
create view public_clusters with (security_invoker = false) as
select c.id, c.city_id, c.domain_id, c.category_id,
       st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lon,
       c.report_count, c.distinct_devices, c.priority_score, c.status,
       c.public_summary, c.dist_to_official_stop_m, c.validated_at, c.last_report_at, c.resolved_at
  from clusters c
 where not c.is_sandbox and c.moderation_status = 'visible' and c.status in ('validated', 'acknowledged', 'resolution_pending', 'resolved');

grant select on public_clusters to anon, authenticated;

create view my_reports with (security_invoker = true) as
select r.id, r.is_sandbox, r.city_id, r.category_id, r.status, r.intake_status, r.severity,
       r.spec ->> 'summary_en' as summary_en, r.raw_text, r.lang, r.moderation_status,
       c.status as cluster_status, r.created_at, r.updated_at
  from reports r left join clusters c on c.id = r.cluster_id
 where r.device_id = auth.uid();

grant select on my_reports to authenticated;
