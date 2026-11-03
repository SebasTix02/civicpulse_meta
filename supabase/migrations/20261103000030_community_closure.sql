-- Community closure, public version (UX flows §3.3, decisions P3 and P7). Until now only the
-- sandbox had it (migration 20, sandbox_submit_closure).
--
-- 1. A neighbour standing at the case (GPS within community_rules.max_distance_m of the case,
--    accuracy at most max_accuracy_m) sends an AFTER photo: submit_community_resolution. The
--    closure stage (Modal) compares it with the case's best BEFORE photo, as for crews. The GPS fix
--    is stored, so the location path can compare when the photos do not align.
-- 2. The AI never closes anything. When it says RESOLVED_CANDIDATE, other neighbours nearby see the
--    pair (nearby_resolutions_to_review) and confirm or deny it (review_community_resolution):
--    `approvals` yes close the case, `rejections` no send it back. Reviewers need trust >= min_trust
--    and at least one report of their own on the map, and never review their own photo.
-- 3. A stakeholder admin can also decide it in the panel (review_resolution), as before.
-- 4. Any other answer (PERSISTENT_ISSUE, NOT_VERIFIABLE_*) stays open for the stakeholder; after
--    expire_days it is rejected without touching anyone's trust (expire_community_resolutions, run by
--    the orchestrator's health cron).
-- A rejected closure sends the case back to the status it had (validated or acknowledged).

set search_path = public, extensions;

insert into app_config (key, value) values
  ('community_rules', '{"max_distance_m": 150, "max_accuracy_m": 50, "review_radius_m": 300, "min_trust": 0.5,
                        "approvals": 2, "rejections": 2, "expire_days": 7, "per_device_day": 5}')
on conflict (key) do nothing;

alter table resolutions add column prior_cluster_status text
  check (prior_cluster_status is null or prior_cluster_status in ('validated', 'acknowledged'));

-- Back to where the case was before the closure photo (acknowledged for older rows).
create or replace function reopen_after_closure(r resolutions, p_actor text) returns text
language plpgsql volatile security definer set search_path = public as $$
declare
  v_from text;
  v_to   text := coalesce(r.prior_cluster_status, 'acknowledged');
begin
  select status into v_from from clusters where id = r.cluster_id for update;
  if v_from = 'resolution_pending' then
    update clusters set status = v_to where id = r.cluster_id;
    insert into cluster_events (cluster_id, from_status, to_status, actor) values (r.cluster_id, v_from, v_to, p_actor);
    return v_to;
  end if;
  return v_from;
end $$;
revoke all on function reopen_after_closure(resolutions, text) from public, anon, authenticated;

create or replace function close_after_closure(r resolutions, p_actor text, p_note text) returns void
language plpgsql volatile security definer set search_path = public as $$
declare v_from text;
begin
  select status into v_from from clusters where id = r.cluster_id for update;
  if v_from = 'resolution_pending' then
    update clusters set status = 'resolved', resolved_at = now() where id = r.cluster_id;
    insert into cluster_events (cluster_id, from_status, to_status, actor) values (r.cluster_id, v_from, 'resolved', p_actor);
  end if;
  update dispatches set status = 'closed', note = coalesce(p_note, 'resolved')
   where cluster_id = r.cluster_id and status = 'accepted';
end $$;
revoke all on function close_after_closure(resolutions, text, text) from public, anon, authenticated;

create or replace function submit_community_resolution(
  p_cluster_id     uuid,
  p_nonce          text,
  p_photo_path     text,
  p_gps_location   jsonb,
  p_gps_accuracy_m real,
  p_width          int default null,
  p_height         int default null,
  p_captured_at    timestamptz default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  rules    jsonb := coalesce((select value from app_config where key = 'community_rules'), '{}');
  uid      uuid := auth.uid();
  dev      devices;
  c        clusters;
  n        capture_nonces;
  v_before reports;
  v_gps    geography;
  v_stake  uuid;
  v_photo  uuid;
  v_res    uuid;
begin
  if uid is null or not is_anonymous_session() then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  select * into dev from devices where id = uid;
  if dev.id is null or dev.is_sandbox then
    raise exception 'invalid_nonce' using errcode = 'PT422';
  end if;
  select * into c from clusters where id = p_cluster_id for update;
  if c.id is null or c.is_sandbox or c.moderation_status <> 'visible' then
    raise exception 'cluster_not_found' using errcode = 'PT404';
  end if;
  if c.status not in ('validated', 'acknowledged') then
    raise exception 'cluster_not_awaiting_fix' using errcode = 'PT409';
  end if;
  if exists (select 1 from resolutions where cluster_id = c.id and outcome = 'open') then
    raise exception 'resolution_already_open' using errcode = 'PT409';
  end if;

  if p_gps_location is null or p_gps_accuracy_m is null or p_gps_accuracy_m <= 0 then
    raise exception 'gps_required' using errcode = 'PT422';
  end if;
  if p_gps_accuracy_m > coalesce((rules ->> 'max_accuracy_m')::real, 50) then
    raise exception 'gps_too_inaccurate' using errcode = 'PT422';
  end if;
  v_gps := st_setsrid(st_makepoint((p_gps_location ->> 'lon')::float8, (p_gps_location ->> 'lat')::float8), 4326)::geography;
  if st_distance(v_gps, c.centroid) > coalesce((rules ->> 'max_distance_m')::real, 150) then
    raise exception 'too_far_from_case' using errcode = 'PT422';
  end if;
  if (select count(*) from resolutions where submitted_by_device = uid and created_at > now() - interval '1 day')
     >= coalesce((rules ->> 'per_device_day')::int, 5) then
    raise exception 'rate_limited' using errcode = 'PT429';
  end if;

  select * into n from capture_nonces where nonce = p_nonce for update;
  if n.nonce is null or n.device_id is distinct from uid or n.purpose <> 'community_resolution' then
    raise exception 'invalid_nonce' using errcode = 'PT422';
  end if;
  if n.used_at is not null then
    raise exception 'nonce_used' using errcode = 'PT409';
  end if;
  if n.expires_at < now() then
    raise exception 'nonce_expired' using errcode = 'PT422';
  end if;
  if p_photo_path <> uid || '/' || p_nonce || '.jpg' then
    raise exception 'photo_path_mismatch' using errcode = 'PT422';
  end if;

  -- The BEFORE side: a visible report with a photo, the one the photo confirmed first.
  select r.* into v_before from reports r
   where r.cluster_id = c.id and r.moderation_status = 'visible' and r.current_photo_version is not null
   order by (r.intake_status = 'REPORT_CONFIRMED_BY_PHOTO') desc nulls last, st_distance(r.geom, c.centroid), r.created_at desc
   limit 1;
  if v_before.id is null then
    raise exception 'no_before_photo' using errcode = 'PT409';
  end if;
  select stakeholder_id into v_stake from dispatches
   where cluster_id = c.id and status = 'accepted' order by created_at limit 1;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values ('raw/' || p_photo_path, p_width, p_height, p_captured_at, false)
  returning id into v_photo;
  insert into resolutions (is_sandbox, cluster_id, before_report_id, photo_id, capture_nonce, closure_path,
                           submitted_by_device, stakeholder_id, geom, gps_accuracy_m, prior_cluster_status)
  values (false, c.id, v_before.id, v_photo, p_nonce, 'community', uid, v_stake, v_gps, p_gps_accuracy_m, c.status)
  returning id into v_res;
  update capture_nonces set used_at = now() where nonce = p_nonce;
  update clusters set status = 'resolution_pending' where id = c.id;
  insert into cluster_events (cluster_id, from_status, to_status, actor) values (c.id, c.status, 'resolution_pending', 'community');

  return jsonb_build_object('resolution_id', v_res, 'cluster_id', c.id, 'ai_status', 'pending',
                            'outcome', 'open', 'cluster_status', 'resolution_pending');
end $$;
revoke all on function submit_community_resolution(uuid, text, text, jsonb, real, int, int, timestamptz) from public, anon;
grant execute on function submit_community_resolution(uuid, text, text, jsonb, real, int, int, timestamptz) to authenticated;

-- Votes of one closure.
create or replace function community_votes(p_resolution_id uuid) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'approvals', count(*) filter (where verdict = 'approve' and reviewer_device is not null),
    'rejections', count(*) filter (where verdict = 'reject' and reviewer_device is not null),
    'needed', coalesce((select (value ->> 'approvals')::int from app_config where key = 'community_rules'), 2))
  from resolution_reviews where resolution_id = p_resolution_id
$$;
revoke all on function community_votes(uuid) from public, anon;
grant execute on function community_votes(uuid) to authenticated;

-- What the neighbour who sent the photo sees (their own closures, newest first).
create or replace view my_community_resolutions with (security_invoker = false) as
select r.id, r.cluster_id, r.ai_status, r.outcome, r.created_at, r.decided_at,
       p.public_path as after_public_path, community_votes(r.id) as votes
  from resolutions r join photos p on p.id = r.photo_id
 where r.closure_path = 'community' and not r.is_sandbox and r.submitted_by_device = auth.uid();
grant select on my_community_resolutions to authenticated;

-- Can this device review closures (P3)? trust >= min_trust and one visible report on the map.
create or replace function can_review_closures(p_device uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from devices d where d.id = p_device and not d.is_sandbox
                    and d.trust >= coalesce((select (value ->> 'min_trust')::real from app_config where key = 'community_rules'), 0.5))
     and exists (select 1 from reports r where r.device_id = p_device and r.status = 'clustered' and r.moderation_status = 'visible')
$$;
revoke all on function can_review_closures(uuid) from public, anon, authenticated;

-- Closures the AI sees as fixed, near the neighbour, that they did not send and have not reviewed.
create or replace function nearby_resolutions_to_review(p_lat double precision, p_lon double precision)
returns jsonb language plpgsql stable security definer set search_path = public, extensions as $$
declare
  rules jsonb := coalesce((select value from app_config where key = 'community_rules'), '{}');
  uid   uuid := auth.uid();
  here  geography := st_setsrid(st_makepoint(p_lon, p_lat), 4326)::geography;
  v_rows jsonb;
begin
  if uid is null then
    return jsonb_build_object('can_review', false, 'items', '[]'::jsonb);
  end if;
  select coalesce(jsonb_agg(x order by (x ->> 'distance_m')::int), '[]') into v_rows
    from (select jsonb_build_object(
                   'resolution_id', r.id, 'cluster_id', c.id, 'category_name', cat.display_name,
                   'resolved_description', cat.resolved_description,
                   'before_public_path', bp.public_path, 'after_public_path', ap.public_path,
                   'distance_m', round(st_distance(c.centroid, here))::int, 'created_at', r.created_at,
                   'votes', community_votes(r.id)) as x
            from resolutions r
            join clusters c on c.id = r.cluster_id
            join categories cat on cat.id = c.category_id
            join photos ap on ap.id = r.photo_id
            join reports b on b.id = r.before_report_id
            join report_photos l on l.report_id = b.id and l.version = b.current_photo_version
            join photos bp on bp.id = l.photo_id
           where r.closure_path = 'community' and not r.is_sandbox and r.outcome = 'open'
             and r.ai_status = 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW'
             and r.submitted_by_device is distinct from uid
             and ap.public_path is not null and bp.public_path is not null
             and not exists (select 1 from resolution_reviews v where v.resolution_id = r.id and v.reviewer_device = uid)
             and st_dwithin(c.centroid, here, coalesce((rules ->> 'review_radius_m')::real, 300))
           order by st_distance(c.centroid, here) limit 10) q;
  return jsonb_build_object('can_review', can_review_closures(uid), 'items', v_rows);
end $$;
revoke all on function nearby_resolutions_to_review(double precision, double precision) from public, anon;
grant execute on function nearby_resolutions_to_review(double precision, double precision) to authenticated;

create or replace function review_community_resolution(p_resolution_id uuid, p_verdict text)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  rules jsonb := coalesce((select value from app_config where key = 'community_rules'), '{}');
  uid   uuid := auth.uid();
  r     resolutions;
  v     jsonb;
  v_out text := 'open';
  v_cluster text;
begin
  if uid is null or not is_anonymous_session() then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  if p_verdict not in ('approve', 'reject') then
    raise exception 'invalid_verdict' using errcode = 'PT400';
  end if;
  select * into r from resolutions where id = p_resolution_id for update;
  if r.id is null or r.is_sandbox or r.closure_path <> 'community' then
    raise exception 'resolution_not_found' using errcode = 'PT404';
  end if;
  if r.outcome <> 'open' then
    raise exception 'already_decided' using errcode = 'PT409';
  end if;
  if r.submitted_by_device = uid then
    raise exception 'self_review' using errcode = 'PT403';
  end if;
  if not can_review_closures(uid) then
    raise exception 'trust_too_low' using errcode = 'PT403';
  end if;
  if r.ai_status = 'pending' then
    raise exception 'ai_pending' using errcode = 'PT409';
  end if;
  if r.ai_status <> 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW' then
    raise exception 'ai_not_candidate' using errcode = 'PT409';
  end if;
  if exists (select 1 from resolution_reviews where resolution_id = r.id and reviewer_device = uid) then
    raise exception 'already_reviewed' using errcode = 'PT409';
  end if;

  insert into resolution_reviews (resolution_id, reviewer_device, verdict) values (r.id, uid, p_verdict);
  v := community_votes(r.id);
  select status into v_cluster from clusters where id = r.cluster_id;
  if (v ->> 'approvals')::int >= coalesce((rules ->> 'approvals')::int, 2) then
    update resolutions set outcome = 'resolved', decided_at = now() where id = r.id;
    perform close_after_closure(r, 'community', 'resolved: confirmed by neighbours');
    v_out := 'resolved';
    v_cluster := 'resolved';
  elsif (v ->> 'rejections')::int >= coalesce((rules ->> 'rejections')::int, 2) then
    update resolutions set outcome = 'rejected', decided_at = now() where id = r.id;
    v_cluster := reopen_after_closure(r, 'community');
    v_out := 'rejected';
  end if;
  return v || jsonb_build_object('resolution_id', r.id, 'outcome', v_out, 'cluster_status', v_cluster);
end $$;
revoke all on function review_community_resolution(uuid, text) from public, anon;
grant execute on function review_community_resolution(uuid, text) to authenticated;

-- P7: a community closure the AI could not confirm waits expire_days for the stakeholder, then it is
-- rejected (no trust change). Candidates keep waiting for neighbours or the stakeholder.
create or replace function expire_community_resolutions() returns int
language plpgsql volatile security definer set search_path = public as $$
declare
  days int := coalesce((select (value ->> 'expire_days')::int from app_config where key = 'community_rules'), 7);
  r resolutions;
  n int := 0;
begin
  for r in select * from resolutions
            where closure_path = 'community' and not is_sandbox and outcome = 'open'
              and ai_status not in ('pending', 'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW')
              and created_at < now() - make_interval(days => days)
            for update skip locked loop
    update resolutions set outcome = 'rejected', decided_at = now() where id = r.id;
    perform reopen_after_closure(r, 'expired');
    n := n + 1;
  end loop;
  return n;
end $$;
revoke all on function expire_community_resolutions() from public, anon, authenticated;
grant execute on function expire_community_resolutions() to service_role;

-- Same as migration 15, except that a rejected closure goes back to the status the case had.
create or replace function review_resolution(p_resolution_id uuid, p_verdict text, p_note text default null)
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

  select * into c from clusters where id = r.cluster_id;
  if p_verdict = 'approve' then
    perform close_after_closure(r, 'stakeholder:' || r.stakeholder_id, v_note);
    v_to := 'resolved';
  else
    v_to := reopen_after_closure(r, 'stakeholder:' || r.stakeholder_id);
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
