-- Depth from one photo agreed only roughly with hand labels (docs/eval/measure_eval_2026-11-02.md:
-- 13/35 exact), so it weighs half of the size: medium +0.25, high +0.5. Every case is recomputed.

set search_path = public, extensions;

update app_config
   set value = jsonb_set(value, '{depth}', '{"low": 0, "medium": 0.25, "high": 0.5}'::jsonb)
 where key = 'priority_rules';

update clusters set priority_context = null;
