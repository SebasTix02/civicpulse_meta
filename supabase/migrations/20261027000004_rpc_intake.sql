-- Citizen intake RPCs (contract: packages/contracts/openapi.yaml).
-- Errors: SQLSTATE 'PT4xx' so PostgREST answers with that HTTP status; message = snake_case code.

set search_path = public, extensions;

create function issue_capture_nonce(p_purpose text default 'report')
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  uid     uuid := auth.uid();
  dev     devices;
  v_nonce text;
  v_exp   timestamptz := now() + interval '5 minutes';
  v_path  text;
begin
  if uid is null then
    raise exception 'not_authenticated' using errcode = 'PT401';
  end if;
  if not is_anonymous_session() then
    raise exception 'actors_cannot_capture' using errcode = 'PT403';
  end if;
  if p_purpose not in ('report', 'report_photo_replacement', 'community_resolution') then
    raise exception 'invalid_purpose' using errcode = 'PT400';
  end if;

  insert into devices (id) values (uid) on conflict (id) do nothing;
  select * into dev from devices where id = uid;

  if (select count(*) from capture_nonces
       where device_id = uid and used_at is null and expires_at > now()) >= 5
     or (select count(*) from capture_nonces
          where device_id = uid and issued_at > now() - interval '1 hour') >= 60
     or (dev.is_sandbox and (select count(*) from capture_nonces
          where device_id = uid and issued_at > now() - interval '1 day') >= 20) then
    raise exception 'rate_limited' using errcode = 'PT429';
  end if;

  v_nonce := translate(encode(gen_random_bytes(16), 'base64'), '+/=', '-_');
  insert into capture_nonces (nonce, device_id, purpose, expires_at) values (v_nonce, uid, p_purpose, v_exp);

  v_path := case when dev.is_sandbox
                 then dev.sandbox_session_id || '/' || uid || '/' || v_nonce || '.jpg'
                 else uid || '/' || v_nonce || '.jpg' end;

  return jsonb_build_object(
    'nonce', v_nonce,
    'expires_at', v_exp,
    'bucket', case when dev.is_sandbox then 'sandbox' else 'raw' end,
    'upload_path', v_path,
    'max_bytes', 2621440);
end $$;

create function submit_report(
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
  existing reports;
  v_geom   geography;
  v_gps    geography;
  v_city   text;
  v_photo  uuid;
  v_ip     text;
  v_expected text;
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
    -- Sandbox samples land on day 6 (cached SAM results); until then they are refused.
    raise exception 'samples_not_available' using errcode = 'PT422';
  end if;
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

  if dev.is_sandbox and (select count(*) from reports
                          where device_id = uid and created_at > now() - interval '1 day') >= 5 then
    raise exception 'sandbox_quota_exceeded' using errcode = 'PT429';
  end if;

  -- /24 for IPv4, /48 for IPv6, from the first x-forwarded-for hop.
  v_ip := split_part(coalesce(current_setting('request.headers', true)::jsonb ->> 'x-forwarded-for', ''), ',', 1);
  v_ip := case when v_ip ~ '^\d+\.\d+\.\d+\.\d+$' then regexp_replace(v_ip, '\.\d+$', '.0/24')
               when v_ip like '%:%' then array_to_string((string_to_array(v_ip, ':'))[1:3], ':') || '::/48'
               else null end;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values ((case when dev.is_sandbox then 'sandbox/' else 'raw/' end) || p_photo_path,
          p_width, p_height, p_captured_at, dev.is_sandbox)
  returning id into v_photo;

  insert into reports (id, is_sandbox, sandbox_session_id, device_id, city_id, current_photo_version,
                       geom, gps_geom, gps_accuracy_m, ip_prefix, raw_text, lang, hint_box)
  values (p_report_id, dev.is_sandbox, dev.sandbox_session_id, uid, v_city, 1,
          v_geom, v_gps, p_gps_accuracy_m, v_ip, coalesce(p_raw_text, ''), p_lang, p_hint_box);

  insert into report_photos (report_id, version, photo_id, capture_nonce)
  values (p_report_id, 1, v_photo, p_nonce);

  update capture_nonces set used_at = now() where nonce = p_nonce;

  return jsonb_build_object('report_id', p_report_id, 'status', 'received', 'photo_version', 1,
                            'city_id', v_city, 'is_sandbox', dev.is_sandbox);
end $$;

-- Effective demo level (riesgos §4): override, else derived from provider health.
create function get_demo_status() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  override jsonb := (select value from app_config where key = 'demo_level_override');
  health   jsonb := (select value from app_config where key = 'provider_health');
  cap      numeric := coalesce((select (value ->> 'usd')::numeric from app_config where key = 'sandbox_daily_cap_usd'), 5);
  spend    jsonb := (select value from app_config where key = 'sandbox_spend');
  lvl      int;
  reason   text;
begin
  if override ->> 'level' is not null then
    lvl := (override ->> 'level')::int; reason := 'override';
  elsif health ->> 'checked_at' is null or (health ->> 'checked_at')::timestamptz < now() - interval '60 minutes' then
    lvl := 3; reason := 'orchestrator_unreachable';
  elsif coalesce(health ->> 'sam', '') <> 'ok'
        or ((spend ->> 'date')::date = current_date and (spend ->> 'usd')::numeric >= cap) then
    lvl := 2; reason := 'sam_unavailable_or_cap';
  elsif coalesce(health ->> 'vision', '') <> 'ok' then
    lvl := 1; reason := 'classifier_unavailable';
  else
    lvl := 0; reason := 'normal';
  end if;
  return jsonb_build_object('level', lvl, 'reason', reason, 'checked_at', health ->> 'checked_at');
end $$;

grant execute on function issue_capture_nonce(text) to authenticated;
grant execute on function submit_report(uuid, jsonb, text, text, text, text, int, int, timestamptz, jsonb, real, text, jsonb) to authenticated;
grant execute on function get_demo_status() to anon, authenticated;
revoke execute on function issue_capture_nonce(text) from anon, public;
revoke execute on function submit_report(uuid, jsonb, text, text, text, text, int, int, timestamptz, jsonb, real, text, jsonb) from anon, public;
