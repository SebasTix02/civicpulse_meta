-- Reused photos (plan §5.5, UX moderation queue M1): the orchestrator stores the photo's sha256
-- and perceptual hash, and this looks for the same picture in any other report (same file, or
-- a hash within p_max_distance bits). A match opens a reused_photo moderation flag; the report
-- stays visible until a moderator decides, but it adds no support (consistency 0, set by the
-- orchestrator). Real and sandbox photos are never compared with each other.

set search_path = public, extensions;

create function check_photo_reuse(p_photo_id uuid, p_sha256 text, p_phash text, p_max_distance int default 10)
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
  select rp.* into r from reports rp join report_photos l on l.report_id = rp.id where l.photo_id = ph.id;

  select coalesce(jsonb_agg(m order by m.distance, m.report_id), '[]') into v_found from (
    select l.report_id, l.version,
           case when o.sha256 = p_sha256 then 0
                else bit_count(('x' || o.phash)::bit(64) # ('x' || p_phash)::bit(64))::int end as distance
      from photos o join report_photos l on l.photo_id = o.id
     where o.id <> ph.id and l.report_id <> r.id and o.is_sandbox = ph.is_sandbox
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

revoke all on function check_photo_reuse(uuid, text, text, int) from public, anon, authenticated;
grant execute on function check_photo_reuse(uuid, text, text, int) to service_role;
