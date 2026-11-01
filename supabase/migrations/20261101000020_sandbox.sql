-- Judges' sandbox (plan §3.6, UX flows §3.4, wireframe C9). Anyone can run the whole cycle
-- without touching real data: report (sample photo or own camera, pin by hand) -> photo check ->
-- consensus completed by simulated neighbours -> brief to the sandbox stakeholder (SBX) -> the
-- visitor plays that stakeholder: accepts, sends the AFTER photo (sample pair or camera) and
-- decides on the AI's closure check.
--
-- The visitor's browser keeps a second anonymous session just for the sandbox, so its device is
-- a sandbox device (migration 4 already routes its uploads, geofence and quota). Everything here
-- runs as that device and only ever reaches rows of its own sandbox_session.
--
-- Cost: samples and own photos run the same live pipeline. Each device may send 5 camera reports,
-- 2 camera closures and 10 sample actions (reports or pairs) a day, and the whole sandbox stops at
-- app_config.sandbox_limits (global reports / closures / new sessions per day): past that the page
-- points to the recorded demo. Expired sessions are deleted by sandbox_cleanup(), which the
-- orchestrator's healthcheck cron calls and which hands back the files to remove from Storage.

set search_path = public, extensions;

-- ---------------------------------------------------------------- samples

-- Sample photos, already anonymized (scripts/make_sandbox_samples.py), in the public bucket.
-- A sample with after_path is a before/after pair: its AFTER photo can close the case it opened.
create table sandbox_samples (
  id             text primary key check (id ~ '^[a-z0-9_]{1,40}$'),
  category_id    text references categories (id),
  title          jsonb not null,
  suggested_text jsonb not null default '{}',
  before_path    text not null unique check (before_path like 'public/samples/%'),
  after_path     text check (after_path like 'public/samples/%'),
  sort           int not null default 0,
  active         boolean not null default true
);
alter table sandbox_samples enable row level security;
create policy read_active_samples on sandbox_samples for select using (active);
grant select on sandbox_samples to anon, authenticated;

insert into app_config (key, value) values
  ('sandbox_limits', '{"camera_reports": 5, "camera_closures": 2, "samples": 10,
                       "global_reports": 300, "global_closures": 100, "global_sessions": 400}')
on conflict (key) do nothing;

-- Shared sample photos are the same file in every session: never a "reused photo".
create or replace function check_photo_reuse(p_photo_id uuid, p_sha256 text, p_phash text, p_max_distance int default 10)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  ph      photos;
  r       reports;
  v_found jsonb;
begin
  if p_phash !~ '^[0-9a-f]{16}$' then
    raise exception 'bad_phash' using errcode = 'PT422';
  end if;
  update photos set sha256 = p_sha256, phash = p_phash where id = p_photo_id returning * into ph;
  if not found then
    raise exception 'photo_not_found' using errcode = 'PT404';
  end if;
  if ph.storage_path like 'public/samples/%' then
    return jsonb_build_object('reused', false, 'matches', '[]'::jsonb, 'sample', true);
  end if;
  select rp.* into r from reports rp join report_photos l on l.report_id = rp.id where l.photo_id = ph.id;

  select coalesce(jsonb_agg(m order by m.distance, m.report_id), '[]') into v_found from (
    select l.report_id, l.version,
           case when o.sha256 = p_sha256 then 0
                else bit_count(('x' || o.phash)::bit(64) # ('x' || p_phash)::bit(64))::int end as distance
      from photos o join report_photos l on l.photo_id = o.id
     where o.id <> ph.id and l.report_id <> r.id and o.is_sandbox = ph.is_sandbox
       and o.storage_path not like 'public/samples/%'
       and (o.sha256 = p_sha256
            or (o.phash ~ '^[0-9a-f]{16}$'
                and bit_count(('x' || o.phash)::bit(64) # ('x' || p_phash)::bit(64)) <= p_max_distance))
     limit 5
  ) m;

  if jsonb_array_length(v_found) > 0 and not exists (
       select 1 from moderation_flags f where f.report_id = r.id and f.reason = 'reused_photo' and f.resolved_at is null) then
    insert into moderation_flags (city_id, report_id, reason, source, evidence)
    values (r.city_id, r.id, 'reused_photo', 'orchestrator', jsonb_build_object('photo_id', ph.id, 'matches', v_found));
  end if;
  return jsonb_build_object('reused', jsonb_array_length(v_found) > 0, 'matches', v_found);
end $$;

-- ---------------------------------------------------------------- session and quota

-- The caller's sandbox device, with a live session; anything else is "start a new session".
create function sandbox_device() returns devices
language plpgsql stable security definer set search_path = public as $$
declare
  dev devices;
begin
  if auth.uid() is null or not is_anonymous_session() then
    raise exception 'sandbox_session_not_found' using errcode = 'PT404';
  end if;
  select d.* into dev from devices d join sandbox_sessions s on s.id = d.sandbox_session_id
   where d.id = auth.uid() and d.is_sandbox and s.expires_at > now();
  if dev.id is null then
    raise exception 'sandbox_session_not_found' using errcode = 'PT404';
  end if;
  return dev;
end $$;
revoke all on function sandbox_device() from public, anon, authenticated;

-- What this device has used in the last 24 hours, and what is left (UX: "Te quedan 4 reportes y 2 cierres").
create function sandbox_quota(p_device uuid) returns jsonb
language sql stable security definer set search_path = public as $$
  with lim as (select value as v from app_config where key = 'sandbox_limits'),
  rep as (
    select count(*) filter (where p.storage_path like 'public/samples/%') as samples,
           count(*) filter (where p.storage_path not like 'public/samples/%') as camera
      from reports r join report_photos l on l.report_id = r.id and l.version = 1 join photos p on p.id = l.photo_id
     where r.device_id = p_device and r.created_at > now() - interval '1 day'),
  res as (
    select count(*) filter (where p.storage_path like 'public/samples/%') as samples,
           count(*) filter (where p.storage_path not like 'public/samples/%') as camera
      from resolutions s join photos p on p.id = s.photo_id
     where s.submitted_by_device = p_device and s.created_at > now() - interval '1 day'),
  glob as (
    select (select count(*) from reports r join devices d on d.id = r.device_id
             where r.is_sandbox and not d.is_simulated and r.created_at > now() - interval '1 day') as reports,
           (select count(*) from resolutions where is_sandbox and created_at > now() - interval '1 day') as closures)
  select jsonb_build_object(
    'camera_reports_left', greatest(0, coalesce((lim.v ->> 'camera_reports')::int, 5) - rep.camera),
    'camera_closures_left', greatest(0, coalesce((lim.v ->> 'camera_closures')::int, 2) - res.camera),
    'samples_left', greatest(0, coalesce((lim.v ->> 'samples')::int, 10) - rep.samples - res.samples),
    'global_reports_open', glob.reports < coalesce((lim.v ->> 'global_reports')::int, 300),
    'global_closures_open', glob.closures < coalesce((lim.v ->> 'global_closures')::int, 100))
  from rep, res, glob left join lim on true
$$;
revoke all on function sandbox_quota(uuid) from public, anon, authenticated;

-- Raises the right error when this action is over a limit. p_kind: camera_report, sample_report,
-- camera_closure, sample_closure.
create function sandbox_take(p_device uuid, p_kind text) returns void
language plpgsql stable security definer set search_path = public as $$
declare
  q jsonb := sandbox_quota(p_device);
begin
  if (p_kind like '%report' and not (q ->> 'global_reports_open')::boolean)
     or (p_kind like '%closure' and not (q ->> 'global_closures_open')::boolean) then
    raise exception 'sandbox_daily_cap' using errcode = 'PT429';
  end if;
  if p_kind like 'sample%' and (q ->> 'samples_left')::int <= 0 then
    raise exception 'sandbox_samples_exceeded' using errcode = 'PT429';
  end if;
  if (p_kind = 'camera_report' and (q ->> 'camera_reports_left')::int <= 0)
     or (p_kind = 'camera_closure' and (q ->> 'camera_closures_left')::int <= 0) then
    raise exception 'sandbox_quota_exceeded' using errcode = 'PT429';
  end if;
end $$;
revoke all on function sandbox_take(uuid, text) from public, anon, authenticated;

-- ---------------------------------------------------------------- what the page shows

create function sandbox_state() returns jsonb
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  dev devices;
  s   sandbox_sessions;
begin
  begin
    dev := sandbox_device();
  exception when sqlstate 'PT404' then
    return jsonb_build_object('session', null);
  end;
  select * into s from sandbox_sessions where id = dev.sandbox_session_id;

  return jsonb_build_object(
    'session', jsonb_build_object('id', s.id, 'city_id', s.city_id, 'expires_at', s.expires_at),
    'quota', sandbox_quota(dev.id),
    'reports', coalesce((
      select jsonb_agg(x order by x.created_at desc) from (
        select r.id, r.created_at, r.status, r.intake_status, r.category_id, cat.display_name as category_name,
               r.cluster_id, k.status as cluster_status, p.public_path as photo_public_path, smp.id as sample_id,
               r.raw_text
          from reports r
          join report_photos l on l.report_id = r.id and l.version = r.current_photo_version
          join photos p on p.id = l.photo_id
          left join sandbox_samples smp on smp.before_path = p.storage_path
          left join categories cat on cat.id = r.category_id
          left join clusters k on k.id = r.cluster_id
         where r.device_id = dev.id and r.sandbox_session_id = s.id
         order by r.created_at desc limit 20) x), '[]'::jsonb),
    'cases', coalesce((
      select jsonb_agg(x order by x.opened_at desc) from (
        select c.id, c.status, c.category_id, cat.display_name as category_name, c.opened_at,
               c.report_count, c.distinct_devices, c.weighted_support,
               st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lon,
               (select count(*) from reports rr join devices dd on dd.id = rr.device_id
                 where rr.cluster_id = c.id and dd.is_simulated) as simulated_reports,
               (select jsonb_build_object('stakeholder_id', d.stakeholder_id, 'stakeholder_name', st.name,
                                          'status', d.status, 'brief', d.brief, 'created_at', d.created_at)
                  from dispatches d join stakeholders st on st.id = d.stakeholder_id
                 where d.cluster_id = c.id order by d.created_at limit 1) as dispatch,
               (select jsonb_build_object(
                         'id', z.id, 'ai_status', z.ai_status, 'outcome', z.outcome, 'ai_overridden', z.ai_overridden,
                         'created_at', z.created_at, 'decided_at', z.decided_at, 'verification', z.verification,
                         'after_public_path', ap.public_path,
                         'before_public_path', (select bp.public_path from report_photos bl join photos bp on bp.id = bl.photo_id
                                                 join reports br on br.id = bl.report_id
                                                 where bl.report_id = z.before_report_id
                                                   and bl.version = br.current_photo_version),
                         'review_note', (select rv.note from resolution_reviews rv
                                          where rv.resolution_id = z.id order by rv.created_at desc limit 1))
                  from resolutions z join photos ap on ap.id = z.photo_id
                 where z.cluster_id = c.id order by z.created_at desc limit 1) as resolution
          from clusters c
          join categories cat on cat.id = c.category_id
         where c.sandbox_session_id = s.id
           and exists (select 1 from reports o where o.cluster_id = c.id and o.device_id = dev.id)
         order by c.opened_at desc limit 20) x), '[]'::jsonb));
end $$;
revoke all on function sandbox_state() from public, anon;
grant execute on function sandbox_state() to authenticated;

-- Opens (or returns) the caller's sandbox session. The browser signs in anonymously with a
-- storage key of its own, so this device is never the citizen's.
create function start_sandbox_session(p_city text default 'ambato') returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  uid  uuid := auth.uid();
  dev  devices;
  s    sandbox_sessions;
  lim  jsonb := coalesce((select value from app_config where key = 'sandbox_limits'), '{}');
  v_id uuid;
begin
  if uid is null then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  if not is_anonymous_session() then
    raise exception 'actors_cannot_capture' using errcode = 'PT403';
  end if;
  if not exists (select 1 from cities where id = p_city and sandbox_enabled) then
    raise exception 'unknown_city' using errcode = 'PT422';
  end if;

  select * into dev from devices where id = uid for update;
  if dev.id is not null and not dev.is_sandbox then
    raise exception 'not_a_sandbox_device' using errcode = 'PT409';
  end if;
  if dev.id is not null then
    select * into s from sandbox_sessions where id = dev.sandbox_session_id;
    if s.expires_at > now() then
      return sandbox_state();
    end if;
  end if;

  if (select count(*) from sandbox_sessions where created_at > now() - interval '1 day')
     >= coalesce((lim ->> 'global_sessions')::int, 400) then
    raise exception 'sandbox_daily_cap' using errcode = 'PT429';
  end if;
  insert into sandbox_sessions (city_id) values (p_city) returning id into v_id;
  if dev.id is null then
    insert into devices (id, is_sandbox, sandbox_session_id) values (uid, true, v_id);
  else
    -- The old session's rows stay with it until sandbox_cleanup() removes them (it expires first).
    update devices set sandbox_session_id = v_id where id = uid;
  end if;
  return sandbox_state();
end $$;
revoke all on function start_sandbox_session(text) from public, anon;
grant execute on function start_sandbox_session(text) to authenticated;

-- ---------------------------------------------------------------- intake: samples

-- Same contract as migration 4. New: p_sample_id (sandbox only) reports with a sample photo,
-- and the per-device sandbox limits come from sandbox_take().
create or replace function submit_report(
  p_report_id        uuid,
  p_location         jsonb,
  p_lang             text,
  p_nonce            text default null,
  p_photo_path       text default null,
  p_sample_id        text default null,
  p_width            int default null,
  p_height           int default null,
  p_captured_at      timestamptz default null,
  p_gps_location     jsonb default null,
  p_gps_accuracy_m   real default null,
  p_raw_text         text default '',
  p_hint_box         jsonb default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  uid      uuid := auth.uid();
  dev      devices;
  n        capture_nonces;
  smp      sandbox_samples;
  existing reports;
  v_geom   geography;
  v_gps    geography;
  v_city   text;
  v_photo  uuid;
  v_ip     text;
  v_expected text;
  v_storage  text;
begin
  if uid is null or not is_anonymous_session() then
    raise exception 'not_a_citizen_session' using errcode = 'PT403';
  end if;
  select * into dev from devices where id = uid;
  if dev.id is null then
    raise exception 'device_not_found' using errcode = 'PT403';
  end if;

  -- Idempotency: same client id from the same device returns the stored result.
  select * into existing from reports where id = p_report_id;
  if existing.id is not null then
    if existing.device_id <> uid then
      raise exception 'report_id_conflict' using errcode = 'PT409';
    end if;
    return jsonb_build_object('report_id', existing.id, 'status', 'received', 'photo_version', 1,
                              'city_id', existing.city_id, 'is_sandbox', existing.is_sandbox);
  end if;

  if p_sample_id is not null then
    if not dev.is_sandbox then
      raise exception 'samples_not_available' using errcode = 'PT422';
    end if;
    select * into smp from sandbox_samples where id = p_sample_id and active;
    if smp.id is null then
      raise exception 'samples_not_available' using errcode = 'PT422';
    end if;
    perform sandbox_device();  -- the session must still be live
    perform sandbox_take(uid, 'sample_report');
    -- report_photos needs a nonce: a spent one, issued here.
    p_nonce := translate(encode(gen_random_bytes(16), 'base64'), '+/=', '-_');
    insert into capture_nonces (nonce, device_id, purpose, used_at) values (p_nonce, uid, 'report', now());
    v_storage := smp.before_path;
  else
    if p_nonce is null or p_photo_path is null then
      raise exception 'photo_required' using errcode = 'PT400';
    end if;
    select * into n from capture_nonces where nonce = p_nonce for update;
    if n.nonce is null or n.device_id <> uid or n.purpose <> 'report' then
      raise exception 'invalid_nonce' using errcode = 'PT422';
    end if;
    if n.used_at is not null then
      raise exception 'nonce_used' using errcode = 'PT409';
    end if;
    if n.expires_at < now() then
      raise exception 'nonce_expired' using errcode = 'PT422';
    end if;
    v_expected := case when dev.is_sandbox
                       then dev.sandbox_session_id || '/' || uid || '/' || p_nonce || '.jpg'
                       else uid || '/' || p_nonce || '.jpg' end;
    if p_photo_path <> v_expected then
      raise exception 'photo_path_mismatch' using errcode = 'PT422';
    end if;
    if dev.is_sandbox then
      perform sandbox_device();
      perform sandbox_take(uid, 'camera_report');
    end if;
    v_storage := (case when dev.is_sandbox then 'sandbox/' else 'raw/' end) || p_photo_path;
  end if;

  v_geom := st_setsrid(st_makepoint((p_location ->> 'lon')::float8, (p_location ->> 'lat')::float8), 4326)::geography;

  if dev.is_sandbox then
    select s.city_id into v_city from sandbox_sessions s where s.id = dev.sandbox_session_id;
  else
    if p_gps_location is null then
      raise exception 'gps_required' using errcode = 'PT400';
    end if;
    if p_gps_accuracy_m is not null and p_gps_accuracy_m > 500 then
      raise exception 'gps_accuracy_too_low' using errcode = 'PT422';
    end if;
    v_gps := st_setsrid(st_makepoint((p_gps_location ->> 'lon')::float8, (p_gps_location ->> 'lat')::float8), 4326)::geography;
    if st_distance(v_geom, v_gps) > 15 then
      raise exception 'pin_too_far' using errcode = 'PT422';
    end if;
    select c.id into v_city from cities c
     where c.is_enabled and st_covers(c.boundary, v_geom) limit 1;
    if v_city is null then
      raise exception 'outside_service_area' using errcode = 'PT422';
    end if;
    if (select count(*) from reports where device_id = uid and created_at > now() - interval '1 day') >= 20 then
      raise exception 'rate_limited' using errcode = 'PT429';
    end if;
  end if;

  -- /24 for IPv4, /48 for IPv6, from the first x-forwarded-for hop.
  v_ip := split_part(coalesce(current_setting('request.headers', true)::jsonb ->> 'x-forwarded-for', ''), ',', 1);
  v_ip := case when v_ip ~ '^\d+\.\d+\.\d+\.\d+$' then regexp_replace(v_ip, '\.\d+$', '.0/24')
               when v_ip like '%:%' then array_to_string((string_to_array(v_ip, ':'))[1:3], ':') || '::/48'
               else null end;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values (v_storage, p_width, p_height, p_captured_at, dev.is_sandbox)
  returning id into v_photo;

  insert into reports (id, is_sandbox, sandbox_session_id, device_id, city_id, current_photo_version,
                       geom, gps_geom, gps_accuracy_m, ip_prefix, raw_text, lang, hint_box)
  values (p_report_id, dev.is_sandbox, dev.sandbox_session_id, uid, v_city, 1,
          v_geom, v_gps, p_gps_accuracy_m, v_ip, coalesce(p_raw_text, ''), p_lang, p_hint_box);

  insert into report_photos (report_id, version, photo_id, capture_nonce)
  values (p_report_id, 1, v_photo, p_nonce);

  update capture_nonces set used_at = now() where nonce = p_nonce and used_at is null;

  return jsonb_build_object('report_id', p_report_id, 'status', 'received', 'photo_version', 1,
                            'city_id', v_city, 'is_sandbox', dev.is_sandbox);
end $$;

-- ---------------------------------------------------------------- consensus, actor, closure

-- Three simulated neighbours confirm the visitor's report once it is on the map (UX: "3 vecinos
-- simulados confirmaron"). Once per case.
create function sandbox_simulate_neighbours(p_report_id uuid) returns jsonb
language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  dev devices := sandbox_device();
  r   reports;
  c   clusters;
begin
  select * into r from reports where id = p_report_id and device_id = dev.id;
  if r.id is null then
    raise exception 'report_not_found' using errcode = 'PT404';
  end if;
  if r.status <> 'clustered' or r.cluster_id is null then
    raise exception 'report_not_ready' using errcode = 'PT409';
  end if;
  if exists (select 1 from reports o join devices d on d.id = o.device_id
              where o.cluster_id = r.cluster_id and d.is_simulated) then
    select * into c from clusters where id = r.cluster_id;
    return jsonb_build_object('cluster_id', c.id, 'status', c.status, 'already', true);
  end if;
  perform simulate_reports(r.sandbox_session_id, r.category_id, st_y(r.geom::geometry), st_x(r.geom::geometry),
                           3, 1, null, 1.0, 10, 0.6);
  select * into c from clusters where id = r.cluster_id;
  return jsonb_build_object('cluster_id', c.id, 'status', c.status, 'already', false,
                            'distinct_devices', c.distinct_devices, 'weighted_support', c.weighted_support);
end $$;
revoke all on function sandbox_simulate_neighbours(uuid) from public, anon;
grant execute on function sandbox_simulate_neighbours(uuid) to authenticated;

-- A case of the caller's session, locked.
create function sandbox_case(p_cluster_id uuid) returns clusters
language plpgsql volatile security definer set search_path = public as $$
declare
  dev devices := sandbox_device();
  c   clusters;
begin
  select * into c from clusters where id = p_cluster_id and sandbox_session_id = dev.sandbox_session_id for update;
  if c.id is null then
    raise exception 'dispatch_not_found' using errcode = 'PT404';
  end if;
  return c;
end $$;
revoke all on function sandbox_case(uuid) from public, anon, authenticated;

-- The visitor accepts the case as the sandbox stakeholder: validated -> acknowledged.
create function sandbox_accept(p_cluster_id uuid) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  c clusters := sandbox_case(p_cluster_id);
  d dispatches;
begin
  select * into d from dispatches where cluster_id = c.id order by created_at limit 1 for update;
  if d.cluster_id is null then
    raise exception 'dispatch_not_found' using errcode = 'PT404';
  end if;
  if d.status = 'accepted' then
    return jsonb_build_object('cluster_id', c.id, 'status', d.status, 'cluster_status', c.status);
  end if;
  if d.status not in ('sent', 'seen') then
    raise exception 'invalid_transition' using errcode = 'PT409';
  end if;
  update dispatches set status = 'accepted', seen_at = coalesce(seen_at, now())
   where cluster_id = d.cluster_id and stakeholder_id = d.stakeholder_id;
  if c.status = 'validated' then
    update clusters set status = 'acknowledged' where id = c.id;
    insert into cluster_events (cluster_id, from_status, to_status, actor)
    values (c.id, 'validated', 'acknowledged', 'sandbox:' || d.stakeholder_id);
    c.status := 'acknowledged';
  end if;
  return jsonb_build_object('cluster_id', c.id, 'status', 'accepted', 'cluster_status', c.status);
end $$;
revoke all on function sandbox_accept(uuid) from public, anon;
grant execute on function sandbox_accept(uuid) to authenticated;

-- The AFTER photo: the AFTER half of a sample pair (only on the case that pair's BEFORE opened)
-- or a camera photo uploaded with a community_resolution nonce. The INSERT fires the resolutions
-- webhook and the orchestrator's closure stage checks it like any other.
create function sandbox_submit_closure(
  p_cluster_id  uuid,
  p_sample_id   text default null,
  p_nonce       text default null,
  p_photo_path  text default null,
  p_width       int default null,
  p_height      int default null,
  p_captured_at timestamptz default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  dev     devices := sandbox_device();
  c       clusters := sandbox_case(p_cluster_id);
  d       dispatches;
  n       capture_nonces;
  smp     sandbox_samples;
  v_before reports;
  v_storage text;
  v_photo uuid;
  v_res   uuid;
begin
  select * into d from dispatches where cluster_id = c.id order by created_at limit 1;
  if d.cluster_id is null or d.status <> 'accepted' then
    raise exception 'dispatch_not_accepted' using errcode = 'PT409';
  end if;
  if c.status <> 'acknowledged' then
    raise exception 'cluster_not_awaiting_fix' using errcode = 'PT409';
  end if;
  if exists (select 1 from resolutions where cluster_id = c.id and outcome = 'open') then
    raise exception 'resolution_already_open' using errcode = 'PT409';
  end if;

  if p_sample_id is not null then
    select * into smp from sandbox_samples where id = p_sample_id and active and after_path is not null;
    if smp.id is null then
      raise exception 'samples_not_available' using errcode = 'PT422';
    end if;
    select r.* into v_before from reports r
      join report_photos l on l.report_id = r.id and l.version = r.current_photo_version
      join photos p on p.id = l.photo_id
     where r.cluster_id = c.id and r.device_id = dev.id and p.storage_path = smp.before_path
     order by r.created_at desc limit 1;
    if v_before.id is null then
      raise exception 'sample_not_in_case' using errcode = 'PT422';
    end if;
    perform sandbox_take(dev.id, 'sample_closure');
    v_storage := smp.after_path;
  else
    if p_nonce is null or p_photo_path is null then
      raise exception 'photo_required' using errcode = 'PT400';
    end if;
    select * into n from capture_nonces where nonce = p_nonce for update;
    if n.nonce is null or n.device_id is distinct from dev.id or n.purpose <> 'community_resolution' then
      raise exception 'invalid_nonce' using errcode = 'PT422';
    end if;
    if n.used_at is not null then
      raise exception 'nonce_used' using errcode = 'PT409';
    end if;
    if n.expires_at < now() then
      raise exception 'nonce_expired' using errcode = 'PT422';
    end if;
    if p_photo_path <> dev.sandbox_session_id || '/' || dev.id || '/' || p_nonce || '.jpg' then
      raise exception 'photo_path_mismatch' using errcode = 'PT422';
    end if;
    select r.* into v_before from reports r
     where r.cluster_id = c.id and r.device_id = dev.id and r.current_photo_version is not null
     order by r.created_at desc limit 1;
    perform sandbox_take(dev.id, 'camera_closure');
    v_storage := 'sandbox/' || p_photo_path;
  end if;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values (v_storage, p_width, p_height, p_captured_at, true)
  returning id into v_photo;

  -- A sample pair was taken at one spot, so its AFTER photo sits on the BEFORE report's pin (the
  -- closure stage may then compare by location). A camera photo has no fix: aligned check only.
  insert into resolutions (is_sandbox, cluster_id, before_report_id, photo_id, capture_nonce, closure_path,
                           submitted_by_device, stakeholder_id, geom, gps_accuracy_m)
  values (true, c.id, v_before.id, v_photo, case when p_sample_id is null then p_nonce end, 'community',
          dev.id, d.stakeholder_id,
          case when p_sample_id is not null then v_before.geom end,
          case when p_sample_id is not null then 10 end)
  returning id into v_res;

  if p_sample_id is null then
    update capture_nonces set used_at = now() where nonce = p_nonce;
  end if;
  update clusters set status = 'resolution_pending' where id = c.id;
  insert into cluster_events (cluster_id, from_status, to_status, actor)
  values (c.id, c.status, 'resolution_pending', 'sandbox:' || d.stakeholder_id);

  return jsonb_build_object('resolution_id', v_res, 'cluster_id', c.id, 'ai_status', 'pending',
                            'outcome', 'open', 'cluster_status', 'resolution_pending');
end $$;
revoke all on function sandbox_submit_closure(uuid, text, text, text, int, int, timestamptz) from public, anon;
grant execute on function sandbox_submit_closure(uuid, text, text, text, int, int, timestamptz) to authenticated;

-- The visitor decides, with the same rules as review_resolution (migration 15): approve only
-- after the AI answered, a note to approve against it; reject sends the case back for a new photo.
create function sandbox_review_closure(p_resolution_id uuid, p_verdict text, p_note text default null)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  dev        devices := sandbox_device();
  r          resolutions;
  c          clusters;
  v_note     text := nullif(btrim(p_note), '');
  v_override boolean;
  v_to       text;
begin
  select z.* into r from resolutions z join clusters k on k.id = z.cluster_id
   where z.id = p_resolution_id and k.sandbox_session_id = dev.sandbox_session_id
   for update of z;
  if r.id is null then
    raise exception 'resolution_not_found' using errcode = 'PT404';
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
  insert into resolution_reviews (resolution_id, reviewer_device, verdict, note)
  values (r.id, dev.id, p_verdict, v_note);

  select * into c from clusters where id = r.cluster_id for update;
  v_to := case when p_verdict = 'approve' then 'resolved' else 'acknowledged' end;
  if c.status = 'resolution_pending' then
    update clusters set status = v_to, resolved_at = case when v_to = 'resolved' then now() end where id = c.id;
    insert into cluster_events (cluster_id, from_status, to_status, actor)
    values (c.id, c.status, v_to, 'sandbox:' || coalesce(r.stakeholder_id::text, 'visitor'));
  end if;
  if p_verdict = 'approve' then
    update dispatches set status = 'closed', note = coalesce(v_note, 'resolved')
     where cluster_id = c.id and status = 'accepted';
  end if;

  return jsonb_build_object('resolution_id', r.id, 'outcome',
                            case when p_verdict = 'approve' then 'resolved' else 'rejected' end,
                            'ai_overridden', v_override, 'cluster_status', v_to);
end $$;
revoke all on function sandbox_review_closure(uuid, text, text) from public, anon;
grant execute on function sandbox_review_closure(uuid, text, text) to authenticated;

-- ---------------------------------------------------------------- cleanup

-- Deletes up to 200 expired sessions (oldest first, so a device that moved to a newer session
-- leaves with its old one's rows already gone) and returns what Storage still holds for them:
-- the orchestrator removes sandbox/<session>/..., public/sandbox/<session>/... and
-- public/sandbox/resolutions/<id>/... (pipeline/sandbox.py).
create function sandbox_cleanup() returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_sessions uuid[];
  v_res      uuid[];
  v_photos   uuid[];
begin
  select array_agg(id) into v_sessions from (
    select id from sandbox_sessions where expires_at < now() order by expires_at limit 200) x;
  if v_sessions is null then
    return jsonb_build_object('sessions', '[]'::jsonb, 'resolutions', '[]'::jsonb);
  end if;
  select array_agg(z.id) into v_res
    from resolutions z join clusters c on c.id = z.cluster_id where c.sandbox_session_id = any(v_sessions);
  select array_agg(distinct photo_id) into v_photos from (
    select l.photo_id from report_photos l join reports r on r.id = l.report_id where r.sandbox_session_id = any(v_sessions)
    union all
    select photo_id from resolutions where id = any(coalesce(v_res, '{}'))) p;

  delete from resolutions where id = any(coalesce(v_res, '{}'));
  delete from sandbox_sessions where id = any(v_sessions);  -- devices, clusters, reports, dispatches cascade
  delete from photos where id = any(coalesce(v_photos, '{}')) and is_sandbox;

  return jsonb_build_object('sessions', to_jsonb(v_sessions), 'resolutions', to_jsonb(coalesce(v_res, '{}')));
end $$;
revoke all on function sandbox_cleanup() from public, anon, authenticated;
grant execute on function sandbox_cleanup() to service_role;
