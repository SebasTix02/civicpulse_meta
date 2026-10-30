-- Public map: every anonymized photo of a case, for the gallery in the case sheet. Same rule as
-- public_clusters (real, visible, validated or later) and only the public copies: faces and
-- plates already blurred, no raw path, no device, no text.

set search_path = public, extensions;

create or replace function public_case_photos(p_cluster_id uuid)
returns table (photo_public_path text, confirmed boolean, created_at timestamptz)
language sql stable security definer set search_path = public, extensions as $$
  select p.public_path, r.intake_status = 'REPORT_CONFIRMED_BY_PHOTO', r.created_at
    from clusters c
    join reports r on r.cluster_id = c.id and r.moderation_status = 'visible'
    join report_photos rp on rp.report_id = r.id and rp.version = r.current_photo_version
    join photos p on p.id = rp.photo_id
   where c.id = p_cluster_id and not c.is_sandbox and c.moderation_status = 'visible'
     and c.status in ('validated', 'acknowledged', 'resolution_pending', 'resolved')
     and p.public_path is not null
   order by (r.intake_status = 'REPORT_CONFIRMED_BY_PHOTO') desc, r.created_at desc
   limit 24;
$$;

revoke all on function public_case_photos(uuid) from public;
grant execute on function public_case_photos(uuid) to anon, authenticated;
