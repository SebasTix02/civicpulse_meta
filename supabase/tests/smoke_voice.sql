-- Smoke test for voice quotas (migration 21) on plain Postgres + local_shim.sql.
\set ON_ERROR_STOP 1
begin;
set local search_path = public, extensions;
update app_config set value = '{"per_user": 2, "global": 3, "max_seconds": 60}' where key = 'voice_limits';

do $$
declare
  a constant text := '{"sub": "7a000000-0000-4000-8000-00000000000a", "role": "authenticated", "is_anonymous": true}';
  b constant text := '{"sub": "7a000000-0000-4000-8000-00000000000b", "role": "authenticated", "is_anonymous": true}';
  ok boolean;
begin
  -- No session: refused.
  perform set_config('request.jwt.claims', '{}', true);
  begin perform take_voice_quota(5); ok := false; exception when others then ok := sqlerrm = 'not_signed_in'; end;
  assert ok, 'needs a session';

  perform set_config('request.jwt.claims', a, true);
  begin perform take_voice_quota(61); ok := false; exception when others then ok := sqlerrm = 'voice_too_long'; end;
  assert ok, 'over max_seconds';
  assert take_voice_quota(12.34) ->> 'left' = '1';
  assert take_voice_quota(3) ->> 'left' = '0';
  begin perform take_voice_quota(3); ok := false; exception when others then ok := sqlerrm = 'voice_quota_exceeded'; end;
  assert ok, 'per-user quota';
  assert (select seconds from voice_usage order by id limit 1) = 12.3, 'rounded seconds';

  -- Another device: one more clip, then the global cap.
  perform set_config('request.jwt.claims', b, true);
  assert take_voice_quota(4) ->> 'left' = '1';
  begin perform take_voice_quota(4); ok := false; exception when others then ok := sqlerrm = 'voice_daily_cap'; end;
  assert ok, 'global cap';

  -- Cleanup keeps today's rows.
  assert voice_usage_cleanup() = 0;
end $$;
rollback;
\echo 'smoke_voice: ok'
