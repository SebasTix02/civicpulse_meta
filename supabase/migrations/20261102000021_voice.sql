-- Voice reports: the citizen may dictate the report instead of typing it. The browser sends a short
-- WAV clip to the orchestrator (POST /transcribe), which calls Muse Voice Transcribe on the Meta
-- Model API and hands back the text. The citizen reads and edits that text before sending; the
-- audio is never stored, only how long it was, here, to cap the cost.
--
-- take_voice_quota runs as the caller (the orchestrator forwards the citizen's own session token),
-- so auth.uid() is the citizen or sandbox device. Limits per day in app_config.voice_limits.

set search_path = public, extensions;

create table voice_usage (
  id         bigint generated always as identity primary key,
  user_id    uuid not null,
  seconds    numeric(6, 1) not null check (seconds > 0 and seconds <= 120),
  created_at timestamptz not null default now()
);
create index voice_usage_user_day on voice_usage (user_id, created_at);
create index voice_usage_day on voice_usage (created_at);
alter table voice_usage enable row level security;  -- no policies: only the function below writes

insert into app_config (key, value) values
  ('voice_limits', '{"per_user": 10, "global": 600, "max_seconds": 60}')
on conflict (key) do nothing;

-- Counts one clip against the caller's daily quota and the global one; returns what is left.
create or replace function take_voice_quota(p_seconds numeric)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  lim      jsonb := coalesce((select value from app_config where key = 'voice_limits'),
                             '{"per_user": 10, "global": 600, "max_seconds": 60}');
  uid      uuid := auth.uid();
  v_user   int;
  v_global int;
begin
  if uid is null then
    raise exception 'not_signed_in' using errcode = 'PT401';
  end if;
  if p_seconds is null or p_seconds <= 0 or p_seconds > (lim ->> 'max_seconds')::numeric then
    raise exception 'voice_too_long' using errcode = 'PT422';
  end if;
  perform pg_advisory_xact_lock(hashtext('voice_quota'));
  select count(*) into v_user from voice_usage where user_id = uid and created_at > now() - interval '1 day';
  if v_user >= (lim ->> 'per_user')::int then
    raise exception 'voice_quota_exceeded' using errcode = 'PT429';
  end if;
  select count(*) into v_global from voice_usage where created_at > now() - interval '1 day';
  if v_global >= (lim ->> 'global')::int then
    raise exception 'voice_daily_cap' using errcode = 'PT429';
  end if;
  insert into voice_usage (user_id, seconds) values (uid, round(p_seconds, 1));
  return jsonb_build_object('left', (lim ->> 'per_user')::int - v_user - 1);
end $$;
revoke all on function take_voice_quota(numeric) from public;
grant execute on function take_voice_quota(numeric) to authenticated;

-- Old rows are only needed for the 24-hour window.
create or replace function voice_usage_cleanup() returns int language sql volatile security definer
set search_path = public as $$
  with gone as (delete from voice_usage where created_at < now() - interval '2 days' returning 1)
  select count(*)::int from gone;
$$;
revoke all on function voice_usage_cleanup() from public;
grant execute on function voice_usage_cleanup() to service_role;
