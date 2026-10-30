-- Public map (home page): the category's display name and one anonymized photo per case, so the
-- map popup needs no second query. Same rows as before: real, visible, validated or later.
-- The photo is the public (anonymized) copy of the latest photo-confirmed visible report.

set search_path = public, extensions;

create or replace view public_clusters with (security_invoker = false) as
select c.id, c.city_id, c.domain_id, c.category_id,
       st_y(c.centroid::geometry) as lat, st_x(c.centroid::geometry) as lon,
       c.report_count, c.distinct_devices, c.priority_score, c.status,
       c.public_summary, c.dist_to_official_stop_m, c.validated_at, c.last_report_at, c.resolved_at,
       cat.display_name as category_name,
       (select p.public_path
          from reports r
          join report_photos rp on rp.report_id = r.id and rp.version = r.current_photo_version
          join photos p on p.id = rp.photo_id
         where r.cluster_id = c.id and r.moderation_status = 'visible'
           and r.intake_status = 'REPORT_CONFIRMED_BY_PHOTO' and p.public_path is not null
         order by r.created_at desc limit 1) as photo_public_path
  from clusters c
  join categories cat on cat.id = c.category_id
 where not c.is_sandbox and c.moderation_status = 'visible' and c.status in ('validated', 'acknowledged', 'resolution_pending', 'resolved');
