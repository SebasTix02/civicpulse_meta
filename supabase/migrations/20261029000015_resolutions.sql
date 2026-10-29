-- Stakeholder closure (API contract: issue_resolution_nonce, submit_stakeholder_resolution,
-- review_resolution; plan §5.4). An actor whose dispatch is accepted uploads an AFTER photo; the
-- INSERT on resolutions fires the webhook and the orchestrator's closure stage writes
-- verification + ai_status. The AI never closes anything: a member with resolutions.approve
-- decides, and approving against the AI needs a note and is recorded as ai_overridden.
--
-- cluster: acknowledged --submit--> resolution_pending --approve--> resolved
--                                                     --reject---> acknowledged

set search_path = public, extensions;

-- Actors are not devices: their nonces carry the user instead.
alter table capture_nonces alter column device_id drop not null;
alter table capture_nonces add column user_id uuid references profiles (user_id) on delete cascade;
alter table capture_nonces add constraint capture_nonces_owner_check check (num_nonnulls(device_id, user_id) = 1);
alter table capture_nonces drop constraint capture_nonces_purpose_check;
alter table capture_nonces add constraint capture_nonces_purpose_check
  check (purpose in ('report', 'report_photo_replacement', 'community_resolution', 'stakeholder_resolution'));
alter table capture_nonces add constraint capture_nonces_actor_purpose_check
  check ((user_id is not null) = (purpose = 'stakeholder_resolution'));

-- One open resolution per cluster at a time.
create unique index resolutions_one_open_idx on resolutions (cluster_id) where outcome = 'open';

-- Is this cluster dispatched to this stakeholder and accepted, with the cluster waiting for a fix?
create function resolution_dispatch(p_cluster_id uuid, p_stakeholder_id uuid)
returns dispatches language plpgsql stable security definer set search_path = public as $$
declare
  d dispatches;
begin
  select * into d from dispatches where cluster_id = p_cluster_id and stakeholder_id = p_stakeholder_id;
  if not found or not member_can('dispatches.read', p_stakeholder_id) then
    raise exception 'dispatch_not_found' using errcode = 'PT404';
  end if;
  if not member_can('resolutions.submit', p_stakeholder_id) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  if d.status <> 'accepted' then
    raise exception 'dispatch_not_accepted' using errcode = 'PT409';
  end if;
  return d;
end $$;
revoke all on function resolution_dispatch(uuid, uuid) from public, anon, authenticated;

create function issue_resolution_nonce(p_cluster_id uuid, p_stakeholder_id uuid)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  uid     uuid := auth.uid();
  d       dispatches;
  v_nonce text;
  v_exp   timestamptz := now() + interval '10 minutes';
  v_path  text;
begin
  if uid is null then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  d := resolution_dispatch(p_cluster_id, p_stakeholder_id);
  if exists (select 1 from resolutions where cluster_id = p_cluster_id and outcome = 'open') then
    raise exception 'resolution_already_open' using errcode = 'PT409';
  end if;
  if (select count(*) from capture_nonces
       where user_id = uid and used_at is null and expires_at > now()) >= 5
     or (select count(*) from capture_nonces
          where user_id = uid and issued_at > now() - interval '1 hour') >= 30 then
    raise exception 'rate_limited' using errcode = 'PT429';
  end if;

  v_nonce := translate(encode(gen_random_bytes(16), 'base64'), '+/=', '-_');
  insert into capture_nonces (nonce, user_id, purpose, expires_at)
  values (v_nonce, uid, 'stakeholder_resolution', v_exp);
  v_path := 'resolutions/' || uid || '/' || v_nonce || '.jpg';

  return jsonb_build_object(
    'nonce', v_nonce,
    'expires_at', v_exp,
    'bucket', case when d.is_sandbox then 'sandbox' else 'raw' end,
    'upload_path', v_path,
    'max_bytes', 2621440);
end $$;
revoke all on function issue_resolution_nonce(uuid, uuid) from public, anon;
grant execute on function issue_resolution_nonce(uuid, uuid) to authenticated;

-- Upload rule: devices as before, plus actors to resolutions/<user>/<nonce>.jpg. The bucket follows
-- the nonce's sandbox flag, which issue_resolution_nonce took from the dispatch.
create or replace function public.can_upload_capture(p_bucket text, p_name text)
returns boolean language sql stable security definer set search_path = public as $$
  select p_bucket in ('raw', 'sandbox') and (
    exists (
      select 1 from capture_nonces n join devices d on d.id = n.device_id
       where n.device_id = auth.uid() and n.used_at is null and n.expires_at > now()
         and p_bucket = case when d.is_sandbox then 'sandbox' else 'raw' end
         and p_name = case when d.is_sandbox
               then d.sandbox_session_id || '/' || d.id || '/' || n.nonce || '.jpg'
               else d.id || '/' || n.nonce || '.jpg' end)
    or exists (
      select 1 from capture_nonces n
       where n.user_id = auth.uid() and n.purpose = 'stakeholder_resolution'
         and n.used_at is null and n.expires_at > now()
         and p_name = 'resolutions/' || n.user_id || '/' || n.nonce || '.jpg'))
$$;

create function submit_stakeholder_resolution(
  p_cluster_id     uuid,
  p_stakeholder_id uuid,
  p_nonce          text,
  p_photo_path     text,
  p_width          int default null,
  p_height         int default null,
  p_captured_at    timestamptz default null,
  p_note           text default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  uid     uuid := auth.uid();
  d       dispatches;
  n       capture_nonces;
  c       clusters;
  v_photo uuid;
  v_res   uuid;
begin
  if uid is null then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  d := resolution_dispatch(p_cluster_id, p_stakeholder_id);

  select * into n from capture_nonces where nonce = p_nonce for update;
  if n.nonce is null or n.user_id is distinct from uid or n.purpose <> 'stakeholder_resolution' then
    raise exception 'invalid_nonce' using errcode = 'PT422';
  end if;
  if n.used_at is not null then
    raise exception 'nonce_used' using errcode = 'PT409';
  end if;
  if n.expires_at < now() then
    raise exception 'nonce_expired' using errcode = 'PT422';
  end if;
  if p_photo_path <> 'resolutions/' || uid || '/' || p_nonce || '.jpg' then
    raise exception 'photo_path_mismatch' using errcode = 'PT422';
  end if;

  select * into c from clusters where id = p_cluster_id for update;
  if c.status <> 'acknowledged' then
    raise exception 'cluster_not_awaiting_fix' using errcode = 'PT409';
  end if;
  if exists (select 1 from resolutions where cluster_id = c.id and outcome = 'open') then
    raise exception 'resolution_already_open' using errcode = 'PT409';
  end if;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values ((case when d.is_sandbox then 'sandbox/' else 'raw/' end) || p_photo_path,
          p_width, p_height, p_captured_at, d.is_sandbox)
  returning id into v_photo;

  insert into resolutions (is_sandbox, cluster_id, photo_id, capture_nonce, closure_path,
                           submitted_by_user, stakeholder_id)
  values (d.is_sandbox, c.id, v_photo, p_nonce, 'stakeholder', uid, p_stakeholder_id)
  returning id into v_res;

  update capture_nonces set used_at = now() where nonce = p_nonce;
  update clusters set status = 'resolution_pending' where id = c.id;
  insert into cluster_events (cluster_id, from_status, to_status, actor)
  values (c.id, c.status, 'resolution_pending', 'stakeholder:' || p_stakeholder_id);
  insert into audit_log (actor_user, city_id, action, entity, entity_id, before, after)
  values (uid, c.city_id, 'resolution.submit', 'resolution', v_res,
          jsonb_build_object('cluster_status', c.status),
          jsonb_build_object('cluster_status', 'resolution_pending', 'note', nullif(btrim(p_note), '')));

  return jsonb_build_object('resolution_id', v_res, 'closure_path', 'stakeholder', 'cluster_id', c.id,
                            'ai_status', 'pending', 'outcome', 'open', 'cluster_status', 'resolution_pending');
end $$;
revoke all on function submit_stakeholder_resolution(uuid, uuid, text, text, int, int, timestamptz, text) from public, anon;
grant execute on function submit_stakeholder_resolution(uuid, uuid, text, text, int, int, timestamptz, text) to authenticated;

-- approve: only once the AI answered, so the approver always sees its result. Resolution resolved,
-- cluster resolved, accepted dispatches closed. Approving anything but RESOLVED_CANDIDATE needs a
-- note. reject: any time (also when the check never finishes); the cluster goes back to
-- acknowledged so a new photo can be sent.
create function review_resolution(p_resolution_id uuid, p_verdict text, p_note text default null)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  uid        uuid := auth.uid();
  r          resolutions;
  c          clusters;
  v_note     text := nullif(btrim(p_note), '');
  v_override boolean;
  v_to       text;
begin
  select * into r from resolutions where id = p_resolution_id for update;
  if not found or r.stakeholder_id is null or not member_can('dispatches.read', r.stakeholder_id) then
    raise exception 'resolution_not_found' using errcode = 'PT404';
  end if;
  if not member_can('resolutions.approve', r.stakeholder_id) then
    raise exception 'forbidden' using errcode = 'PT403';
  end if;
  if p_verdict not in ('approve', 'reject') then
    raise exception 'invalid_verdict' using errcode = 'PT400';
  end if;
  if r.outcome <> 'open' then
    raise exception 'already_decided' using errcode = 'PT409';
  end if;
  if p_verdict = 'approve' and r.ai_status = 'pending' then
    raise exception 'ai_pending' using errcode = 'PT409';
  end if;
  v_override := p_verdict = 'approve' and r.ai_status <> 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW';
  if v_override and v_note is null then
    raise exception 'note_required' using errcode = 'PT422';
  end if;

  update resolutions
     set outcome = case when p_verdict = 'approve' then 'resolved' else 'rejected' end,
         ai_overridden = v_override, decided_at = now()
   where id = r.id;
  insert into resolution_reviews (resolution_id, reviewer_user, verdict, note)
  values (r.id, uid, p_verdict, v_note);

  select * into c from clusters where id = r.cluster_id for update;
  v_to := case when p_verdict = 'approve' then 'resolved' else 'acknowledged' end;
  if c.status = 'resolution_pending' then
    update clusters set status = v_to, resolved_at = case when v_to = 'resolved' then now() end where id = c.id;
    insert into cluster_events (cluster_id, from_status, to_status, actor)
    values (c.id, c.status, v_to, 'stakeholder:' || r.stakeholder_id);
  end if;
  if p_verdict = 'approve' then
    update dispatches set status = 'closed', note = coalesce(v_note, 'resolved')
     where cluster_id = c.id and status = 'accepted';
  end if;

  insert into audit_log (actor_user, city_id, action, entity, entity_id, before, after)
  values (uid, c.city_id, 'resolution.' || p_verdict, 'resolution', r.id,
          jsonb_build_object('ai_status', r.ai_status, 'cluster_status', c.status),
          jsonb_build_object('outcome', case when p_verdict = 'approve' then 'resolved' else 'rejected' end,
                             'ai_overridden', v_override, 'note', v_note));

  return jsonb_build_object('resolution_id', r.id, 'outcome',
                            case when p_verdict = 'approve' then 'resolved' else 'rejected' end,
                            'ai_overridden', v_override, 'cluster_status', v_to);
end $$;
revoke all on function review_resolution(uuid, text, text) from public, anon;
grant execute on function review_resolution(uuid, text, text) to authenticated;

-- The panel shows the latest resolution of each case, with the AI's figures (public bucket only).
create or replace view panel_inbox with (security_invoker = false) as
select d.cluster_id, d.stakeholder_id, s.name as stakeholder_name, d.is_sandbox,
       d.status as dispatch_status, d.created_at as dispatched_at, d.seen_at, d.note, d.brief,
       c.priority_score,
       jsonb_build_object(
         'id', c.id, 'city_id', c.city_id, 'domain_id', c.domain_id, 'category_id', c.category_id,
         'category_name', cat.display_name,
         'lat', st_y(c.centroid::geometry), 'lon', st_x(c.centroid::geometry),
         'report_count', c.report_count, 'distinct_devices', c.distinct_devices, 'status', c.status,
         'validated_at', c.validated_at, 'last_report_at', c.last_report_at, 'resolved_at', c.resolved_at) as cluster,
       (select jsonb_build_object(
                 'id', r.id, 'ai_status', r.ai_status, 'outcome', r.outcome, 'ai_overridden', r.ai_overridden,
                 'created_at', r.created_at, 'decided_at', r.decided_at,
                 'after_public_path', p.public_path, 'verification', r.verification,
                 'can_review', member_can('resolutions.approve', r.stakeholder_id),
                 'review_note', (select rr.note from resolution_reviews rr
                                  where rr.resolution_id = r.id order by rr.created_at desc limit 1))
          from resolutions r join photos p on p.id = r.photo_id
         where r.cluster_id = d.cluster_id and r.stakeholder_id = d.stakeholder_id
         order by r.created_at desc limit 1) as latest_resolution,
       member_can('resolutions.submit', d.stakeholder_id) as can_submit_resolution
  from dispatches d
  join stakeholders s on s.id = d.stakeholder_id
  join clusters c on c.id = d.cluster_id
  join categories cat on cat.id = c.category_id
 where d.brief is not null and c.moderation_status = 'visible'
   and member_can('dispatches.read', d.stakeholder_id);
