-- Stakeholder panel (API contract: panel_inbox, panel_cluster_reports, set_dispatch_status).
-- Actors are invited users (no open sign-up, scripts/invite_actor.py). What they may see and do
-- is checked against user_roles in the database by member_can(), so a role granted or revoked
-- takes effect at once and the panel does not depend on the custom access token hook.
--
-- A dispatch only shows up once its brief is written. Sandbox dispatches only reach members of
-- the sandbox stakeholder; real stakeholders never see sandbox rows (dispatches FKs).

set search_path = public, extensions;

-- Does the signed-in user hold `perm` for this stakeholder (stakeholder, city or global role)?
create function member_can(perm text, p_stakeholder uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select not coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) and exists (
    select 1
      from user_roles ur
      join roles r on r.id = ur.role_id
      join role_permissions rp on rp.role_id = ur.role_id and rp.permission_id = perm
      join profiles p on p.user_id = ur.user_id and p.is_active
      left join stakeholders s on s.id = p_stakeholder
     where ur.user_id = auth.uid() and ur.revoked_at is null
       and (r.scope = 'global'
            or (r.scope = 'city' and ur.city_id = s.city_id)
            or (r.scope = 'stakeholder' and ur.stakeholder_id = p_stakeholder)))
$$;
grant execute on function member_can(text, uuid) to authenticated;

-- Stakeholders I can act for (the panel's switcher).
create view my_stakeholders with (security_invoker = false) as
select s.id, s.name, s.kind, s.city_id, s.is_sandbox, s.brief_lang,
       member_can('dispatches.respond', s.id) as can_respond
  from stakeholders s
 where member_can('dispatches.read', s.id);
grant select on my_stakeholders to authenticated;

create view panel_inbox with (security_invoker = false) as
select d.cluster_id, d.stakeholder_id, s.name as stakeholder_name, d.is_sandbox,
       d.status as dispatch_status, d.created_at as dispatched_at, d.seen_at, d.note, d.brief,
       c.priority_score,
       jsonb_build_object(
         'id', c.id, 'city_id', c.city_id, 'domain_id', c.domain_id, 'category_id', c.category_id,
         'category_name', cat.display_name,
         'lat', st_y(c.centroid::geometry), 'lon', st_x(c.centroid::geometry),
         'report_count', c.report_count, 'distinct_devices', c.distinct_devices, 'status', c.status,
         'validated_at', c.validated_at, 'last_report_at', c.last_report_at, 'resolved_at', c.resolved_at) as cluster
  from dispatches d
  join stakeholders s on s.id = d.stakeholder_id
  join clusters c on c.id = d.cluster_id
  join categories cat on cat.id = c.category_id
 where d.brief is not null and c.moderation_status = 'visible'
   and member_can('dispatches.read', d.stakeholder_id);
grant select on panel_inbox to authenticated;

-- Reports of the clusters dispatched to me: never device, network or raw photo.
create view panel_cluster_reports with (security_invoker = false) as
select r.id as report_id, r.cluster_id, 'r_' || left(r.id::text, 8) as short_ref,
       r.spec ->> 'summary_en' as summary_en, r.raw_text_redacted, r.lang, r.severity, r.intake_status,
       p.public_path as photo_public_path, r.created_at
  from reports r
  left join report_photos rp on rp.report_id = r.id and rp.version = r.current_photo_version
  left join photos p on p.id = rp.photo_id
 where r.moderation_status = 'visible'
   and exists (select 1 from dispatches d
                where d.cluster_id = r.cluster_id and member_can('dispatches.read', d.stakeholder_id));
grant select on panel_cluster_reports to authenticated;

-- sent -> seen; sent|seen -> accepted|declined; accepted -> closed. `accepted` moves the cluster
-- from validated to acknowledged; declined and closed need a note. Everything is audited.
create function set_dispatch_status(p_cluster_id uuid, p_stakeholder_id uuid, p_status text, p_note text default null)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  d  dispatches;
  c  clusters;
  ok boolean;
begin
  select * into d from dispatches where cluster_id = p_cluster_id and stakeholder_id = p_stakeholder_id for update;
  if not found or not member_can('dispatches.read', p_stakeholder_id) then
    raise exception 'dispatch_not_found' using errcode = 'PT404';
  end if;
  if p_status <> 'seen' and not member_can('dispatches.respond', p_stakeholder_id) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  ok := case p_status
          when 'seen' then d.status = 'sent'
          when 'accepted' then d.status in ('sent', 'seen')
          when 'declined' then d.status in ('sent', 'seen')
          when 'closed' then d.status = 'accepted'
          else false end;
  if not ok then
    if p_status = 'seen' then  -- opening a case twice is not an error
      return jsonb_build_object('cluster_id', d.cluster_id, 'stakeholder_id', d.stakeholder_id, 'status', d.status);
    end if;
    raise exception 'invalid_transition' using errcode = 'PT409';
  end if;
  if p_status in ('declined', 'closed') and coalesce(btrim(p_note), '') = '' then
    raise exception 'note_required' using errcode = 'PT422';
  end if;

  update dispatches
     set status = p_status, note = coalesce(nullif(btrim(p_note), ''), note),
         seen_at = coalesce(seen_at, now())
   where cluster_id = p_cluster_id and stakeholder_id = p_stakeholder_id;

  select * into c from clusters where id = p_cluster_id for update;
  if p_status = 'accepted' and c.status = 'validated' then
    update clusters set status = 'acknowledged' where id = c.id;
    insert into cluster_events (cluster_id, from_status, to_status, actor)
    values (c.id, 'validated', 'acknowledged', 'stakeholder:' || p_stakeholder_id);
    c.status := 'acknowledged';
  end if;

  insert into audit_log (actor_user, city_id, action, entity, entity_id, before, after)
  values (auth.uid(), c.city_id, 'dispatch.' || p_status, 'dispatch', p_cluster_id || ':' || p_stakeholder_id,
          jsonb_build_object('status', d.status), jsonb_build_object('status', p_status, 'note', p_note));

  return jsonb_build_object('cluster_id', p_cluster_id, 'stakeholder_id', p_stakeholder_id,
                            'status', p_status, 'cluster_status', c.status);
end $$;
revoke all on function set_dispatch_status(uuid, uuid, text, text) from public, anon;
grant execute on function set_dispatch_status(uuid, uuid, text, text) to authenticated;
