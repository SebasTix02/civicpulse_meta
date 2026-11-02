-- Case priority, third part: relative size and estimated depth from the photo (civicpulse/measure.py).
--
-- The vision stage writes reports.intake.measure when a photo confirms a pothole:
--   size  = pothole width / roadway width in the same rows (small / medium / large), and `clipped`
--           when the street runs off both sides of the photo (then the share is only a maximum);
--   depth = low / medium / high from visual cues (exposed base, steep edges, water) and the text.
-- The case takes the largest unclipped size and the deepest level among its visible reports.
-- A clipped size is shown but adds nothing. Weights join app_config.priority_rules.

set search_path = public, extensions;

update app_config
   set value = value || '{"size": {"small": 0, "medium": 0.5, "large": 1.0},
                          "depth": {"low": 0, "medium": 0.5, "high": 1.0}}'::jsonb
 where key = 'priority_rules' and not value ? 'size';

create or replace function cluster_measure_context(p_cluster uuid) returns jsonb
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  rules   jsonb := coalesce((select value from app_config where key = 'priority_rules'), '{}');
  v_size  jsonb;
  v_depth text;
  b_size  real;
  b_depth real;
begin
  select jsonb_build_object('class', s ->> 'size_class', 'width_share', (s ->> 'width_share')::real,
                            'clipped', coalesce((s ->> 'clipped')::boolean, false))
    into v_size
    from reports r, lateral (select r.intake -> 'measure' -> 'size' as s) m
   where r.cluster_id = p_cluster and r.moderation_status = 'visible' and s ->> 'status' = 'ok'
   order by coalesce((s ->> 'clipped')::boolean, false), (s ->> 'width_share')::real desc
   limit 1;

  select lvl into v_depth
    from (select r.intake -> 'measure' -> 'depth' ->> 'level' as lvl
            from reports r
           where r.cluster_id = p_cluster and r.moderation_status = 'visible') d
   where lvl in ('low', 'medium', 'high')
   order by array_position(array['high', 'medium', 'low'], lvl)
   limit 1;

  b_size := case when v_size is null or (v_size ->> 'clipped')::boolean then 0
                 else coalesce((rules -> 'size' ->> (v_size ->> 'class'))::real, 0) end;
  b_depth := coalesce((rules -> 'depth' ->> v_depth)::real, 0);
  return jsonb_build_object('size', v_size, 'depth', v_depth, 'b_size', b_size, 'b_depth', b_depth);
end $$;
revoke all on function cluster_measure_context(uuid) from public, anon, authenticated;

-- Same as migration 27, plus the size and depth bonuses.
create or replace function clusters_priority() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
declare
  v_base real;
  v_ctx  jsonb;
  v_m    jsonb;
  v_total real;
begin
  if tg_op = 'INSERT' or new.priority_score is distinct from old.priority_score then
    v_base := new.priority_score;
  else
    v_base := coalesce((old.priority_context ->> 'base')::real, old.priority_score);
  end if;
  v_ctx := cluster_priority_context(new.id, new.city_id, new.centroid);
  v_m := cluster_measure_context(new.id);
  v_total := (v_ctx ->> 'bonus_total')::real + (v_m ->> 'b_size')::real + (v_m ->> 'b_depth')::real;
  new.priority_context := v_ctx || jsonb_build_object(
    'base', v_base, 'size', v_m -> 'size', 'depth', v_m -> 'depth',
    'bonus', (v_ctx -> 'bonus') || jsonb_build_object('size', v_m -> 'b_size', 'depth', v_m -> 'b_depth'),
    'bonus_total', round(v_total::numeric, 3));
  new.priority_score := round((v_base + v_total)::numeric, 3);
  return new;
end $$;

update clusters set priority_context = null;
