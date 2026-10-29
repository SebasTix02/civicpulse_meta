-- GPS as a second way to verify a closure (sebas, 2026-10-29). Real AFTER photos are often taken
-- from another angle and do not register; the phone's GPS fix of the AFTER photo, compared with the
-- BEFORE report's, lets the closure stage compare the photos without alignment (evidence =
-- location, stricter thresholds). The fix is optional: without it only the aligned path exists.

set search_path = public, extensions;

alter table resolutions add column gps_accuracy_m real check (gps_accuracy_m is null or gps_accuracy_m > 0);

drop function submit_stakeholder_resolution(uuid, uuid, text, text, int, int, timestamptz, text);

create function submit_stakeholder_resolution(
  p_cluster_id     uuid,
  p_stakeholder_id uuid,
  p_nonce          text,
  p_photo_path     text,
  p_width          int default null,
  p_height         int default null,
  p_captured_at    timestamptz default null,
  p_note           text default null,
  p_gps_location   jsonb default null,
  p_gps_accuracy_m real default null)
returns jsonb language plpgsql volatile security definer set search_path = public, extensions as $$
declare
  uid     uuid := auth.uid();
  d       dispatches;
  n       capture_nonces;
  c       clusters;
  v_photo uuid;
  v_res   uuid;
  v_gps   geography;
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

  if p_gps_location is not null then
    if p_gps_accuracy_m is null or p_gps_accuracy_m <= 0 then
      raise exception 'gps_accuracy_required' using errcode = 'PT422';
    end if;
    v_gps := st_setsrid(st_makepoint((p_gps_location ->> 'lon')::float8, (p_gps_location ->> 'lat')::float8), 4326)::geography;
  end if;

  insert into photos (storage_path, width, height, captured_at, is_sandbox)
  values ((case when d.is_sandbox then 'sandbox/' else 'raw/' end) || p_photo_path,
          p_width, p_height, p_captured_at, d.is_sandbox)
  returning id into v_photo;

  insert into resolutions (is_sandbox, cluster_id, photo_id, capture_nonce, closure_path,
                           submitted_by_user, stakeholder_id, geom, gps_accuracy_m)
  values (d.is_sandbox, c.id, v_photo, p_nonce, 'stakeholder', uid, p_stakeholder_id, v_gps, p_gps_accuracy_m)
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
revoke all on function submit_stakeholder_resolution(uuid, uuid, text, text, int, int, timestamptz, text, jsonb, real) from public, anon;
grant execute on function submit_stakeholder_resolution(uuid, uuid, text, text, int, int, timestamptz, text, jsonb, real) to authenticated;

-- For the closure stage (service role): AFTER fix vs the BEFORE report's fix. A report without a
-- GPS fix (sandbox, simulated) falls back to its pin with an assumed 15 m error.
create function closure_location(p_resolution_id uuid, p_before_report_id uuid)
returns jsonb language sql stable security definer set search_path = public, extensions as $$
  select jsonb_build_object(
           'distance_m', st_distance(r.geom, coalesce(b.gps_geom, b.geom)),
           'after_accuracy_m', r.gps_accuracy_m,
           'before_accuracy_m', case when b.gps_geom is not null then coalesce(b.gps_accuracy_m, 15) else 15 end)
    from resolutions r, reports b
   where r.id = p_resolution_id and b.id = p_before_report_id and b.cluster_id = r.cluster_id
$$;
revoke all on function closure_location(uuid, uuid) from public, anon, authenticated;
grant execute on function closure_location(uuid, uuid) to service_role;
