-- Routing (plan §3.3 step 7): when a cluster becomes validated, every stakeholder that covers its
-- domain (and category, when the coverage names one) and whose area contains it (or has no
-- area) gets a dispatch. Real clusters only go to verified stakeholders; sandbox clusters only
-- to sandbox stakeholders. Each new dispatch fires the dispatches webhook, and the orchestrator
-- writes its brief (day 7). A stakeholder is dispatched once per cluster.

set search_path = public, extensions;

create function route_validated_cluster() returns trigger language plpgsql security definer
set search_path = public, extensions as $$
begin
  insert into dispatches (cluster_id, stakeholder_id, is_sandbox)
  select distinct new.id, s.id, new.is_sandbox
    from stakeholders s join stakeholder_coverage sc on sc.stakeholder_id = s.id
   where s.city_id = new.city_id and s.is_sandbox = new.is_sandbox and (s.verified or s.is_sandbox)
     and sc.domain_id = new.domain_id and (sc.category_id is null or sc.category_id = new.category_id)
     and (s.area is null or st_covers(s.area, new.centroid))
  on conflict (cluster_id, stakeholder_id) do nothing;
  return null;
end $$;

create trigger clusters_route_on_validation after update of status on clusters
  for each row when (old.status = 'candidate' and new.status = 'validated')
  execute function route_validated_cluster();
