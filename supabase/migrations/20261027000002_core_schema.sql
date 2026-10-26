-- CivicPulse core schema (E-R v2.2).
-- Identifiers, enums and states are English. States are text + CHECK so new values
-- need one migration, not an enum rewrite.
-- Sandbox isolation: every row that can be sandbox carries is_sandbox, and the
-- composite FKs (id, is_sandbox) make it impossible to mix sandbox and real rows.

set search_path = public, extensions;

-- ---------------------------------------------------------------- catalog

create table domains (
  id                text primary key check (id ~ '^[a-z][a-z0-9_]{1,31}$'),
  display_name      jsonb not null,
  status            text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  count_prompts     text[] not null default '{}',
  stakeholder_kinds text[] not null default '{}',
  action_catalog    text[] not null default '{}',
  spatial_layers    text[] not null default '{}',
  updated_at        timestamptz not null default now()
);

create table profiles (
  user_id      uuid primary key references auth.users (id) on delete cascade,
  display_name text not null,
  locale       text not null default 'es' check (locale in ('es', 'en')),
  is_active    boolean not null default true,
  created_at   timestamptz not null default now()
);

create table categories (
  id                   text primary key check (id ~ '^[a-z][a-z0-9_]{1,47}$'),
  family               text not null,
  status               text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  current_version      int not null default 1,
  display_name         jsonb not null,
  keywords             jsonb not null default '{"en": [], "es": []}',
  keyword_priority     smallint not null default 0,
  photo_verifiability  text not null check (photo_verifiability in ('full', 'partial', 'none')),
  default_priority     text not null default 'medium' check (default_priority in ('low', 'medium', 'high', 'critical')),
  context_prompts      text[] not null default '{}',
  target_prompts       text[] not null default '{}',
  extra_target_prompts text[] not null default '{}',
  resolved_prompts     text[] not null default '{}',
  confuser_prompts     text[] not null default '{}',
  issue_can_be_global  boolean not null default false,
  issue_description    text,
  resolved_description text,
  vlm_hints            text,
  created_by           uuid references profiles (user_id),
  updated_at           timestamptz not null default now()
);

create table category_versions (
  category_id text not null references categories (id),
  version     int not null,
  snapshot    jsonb not null,
  created_at  timestamptz not null default now(),
  primary key (category_id, version)
);

create table domain_categories (
  domain_id   text not null references domains (id),
  category_id text not null references categories (id),
  primary key (domain_id, category_id)
);

-- ---------------------------------------------------------------- geography

create table cities (
  id              text primary key,
  name            text not null,
  boundary        geography(multipolygon, 4326),
  default_lang    text not null default 'es',
  timezone        text not null default 'America/Guayaquil',
  is_enabled      boolean not null default false,
  sandbox_enabled boolean not null default true
);

create table geo_features (
  id         uuid primary key default gen_random_uuid(),
  city_id    text not null references cities (id),
  layer      text not null check (layer in ('transit_stops', 'transit_routes', 'roads', 'schools', 'hospitals', 'markets', 'neighborhoods')),
  source     text not null,
  source_ref text not null unique,
  name       text,
  official   boolean,
  geom       geography not null,
  props      jsonb not null default '{}'
);
create index geo_features_geom_idx on geo_features using gist (geom);
create index geo_features_layer_idx on geo_features (city_id, layer);

-- ---------------------------------------------------------------- identity, roles, permissions

create table roles (
  id           text primary key,
  display_name jsonb not null,
  scope        text not null check (scope in ('global', 'city', 'stakeholder'))
);

create table permissions (
  id          text primary key,
  description text not null
);

create table role_permissions (
  role_id       text not null references roles (id),
  permission_id text not null references permissions (id),
  primary key (role_id, permission_id)
);

create table sandbox_sessions (
  id         uuid primary key default gen_random_uuid(),
  city_id    text not null references cities (id),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '24 hours'
);

create table stakeholders (
  id            uuid primary key default gen_random_uuid(),
  is_sandbox    boolean not null default false,
  city_id       text not null references cities (id),
  name          text not null,
  kind          text not null check (kind in ('transit_operator', 'government', 'utility', 'university', 'neighborhood', 'media')),
  area          geography(multipolygon, 4326),
  brief_profile text not null default 'operational' check (brief_profile in ('operational', 'technical', 'citizen', 'press')),
  brief_lang    text not null default 'es',
  verified      boolean not null default false,
  unique (id, is_sandbox)
);

create table user_roles (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references profiles (user_id),
  role_id        text not null references roles (id),
  city_id        text references cities (id),
  stakeholder_id uuid references stakeholders (id),
  granted_by     uuid references profiles (user_id),
  granted_at     timestamptz not null default now(),
  revoked_at     timestamptz
);
create index user_roles_user_idx on user_roles (user_id) where revoked_at is null;

create table audit_log (
  id         bigint generated always as identity primary key,
  actor_user uuid references profiles (user_id),
  city_id    text references cities (id),
  action     text not null,
  entity     text not null,
  entity_id  text not null,
  before     jsonb,
  after      jsonb,
  at         timestamptz not null default now()
);

create table app_config (
  key        text primary key,
  value      jsonb not null,
  updated_by uuid references profiles (user_id),
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- citizens and ingestion

create table devices (
  id                 uuid primary key, -- = auth.uid() of the anonymous session
  is_sandbox         boolean not null default false,
  sandbox_session_id uuid references sandbox_sessions (id) on delete cascade,
  is_simulated       boolean not null default false,
  trust              real not null default 0.5 check (trust between 0 and 1),
  confirmed_count    int not null default 0,
  rejected_count     int not null default 0,
  first_seen         timestamptz not null default now(),
  unique (id, is_sandbox),
  check (is_sandbox = (sandbox_session_id is not null))
);

create table capture_nonces (
  nonce      text primary key,
  device_id  uuid not null references devices (id) on delete cascade,
  purpose    text not null default 'report' check (purpose in ('report', 'report_photo_replacement', 'community_resolution')),
  issued_at  timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '5 minutes',
  used_at    timestamptz
);

create table photos (
  id           uuid primary key default gen_random_uuid(),
  storage_path text not null,
  public_path  text,
  sha256       text,
  phash        text,
  width        int,
  height       int,
  captured_at  timestamptz,
  is_sandbox   boolean not null default false
);
create index photos_sha256_idx on photos (sha256);
create index photos_phash_idx on photos (phash);

create table clusters (
  id                      uuid primary key default gen_random_uuid(),
  is_sandbox              boolean not null default false,
  sandbox_session_id      uuid references sandbox_sessions (id) on delete cascade,
  city_id                 text not null references cities (id),
  domain_id               text not null,
  category_id             text not null,
  centroid                geography(point, 4326) not null,
  report_count            int not null default 0,
  distinct_devices        int not null default 0,
  weighted_support        real not null default 0,
  priority_score          real not null default 0,
  dist_to_official_stop_m real,
  status                  text not null default 'candidate'
                          check (status in ('candidate', 'validated', 'acknowledged', 'resolution_pending', 'resolved')),
  public_summary          jsonb,
  moderation_status       text not null default 'visible' check (moderation_status in ('visible', 'hidden')),
  opened_at               timestamptz not null default now(),
  validated_at            timestamptz,
  last_report_at          timestamptz,
  resolved_at             timestamptz,
  unique (id, is_sandbox),
  foreign key (domain_id, category_id) references domain_categories (domain_id, category_id)
);
create index clusters_centroid_idx on clusters using gist (centroid);

create table reports (
  id                    uuid primary key, -- generated by the client: makes submit_report idempotent
  is_sandbox            boolean not null default false,
  sandbox_session_id    uuid references sandbox_sessions (id) on delete cascade,
  device_id             uuid not null,
  city_id               text not null references cities (id),
  current_photo_version int,
  domain_id             text,
  category_id           text,
  catalog_version       int,
  cluster_id            uuid,
  geom                  geography(point, 4326) not null,
  gps_geom              geography(point, 4326),
  gps_accuracy_m        real,
  ip_prefix             text,
  raw_text              text not null default '' check (length(raw_text) <= 2000),
  raw_text_redacted     text,
  lang                  text,
  hint_box              jsonb,
  status                text not null default 'received'
                        check (status in ('received', 'spec_ready', 'vision_done', 'clustered', 'failed')),
  severity              smallint check (severity between 1 and 5),
  spec                  jsonb,
  intake                jsonb,
  intake_status         text check (intake_status in (
                          'REPORT_CONFIRMED_BY_PHOTO',
                          'ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW',
                          'CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW',
                          'NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO',
                          'NOT_VERIFIABLE_NO_VISUAL_SPEC',
                          'NOT_PHOTO_VERIFIABLE_CATEGORY')),
  consistency_score     real,
  model_versions        jsonb,
  moderation_status     text not null default 'visible' check (moderation_status in ('visible', 'hidden')),
  moderation_reason     text check (moderation_reason in ('spam', 'reused_photo', 'personal_data', 'inappropriate', 'impossible_gps', 'duplicate', 'other')),
  moderated_by          uuid references profiles (user_id),
  moderated_at          timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (id, is_sandbox),
  foreign key (device_id, is_sandbox) references devices (id, is_sandbox),
  foreign key (cluster_id, is_sandbox) references clusters (id, is_sandbox),
  foreign key (domain_id, category_id) references domain_categories (domain_id, category_id),
  foreign key (category_id, catalog_version) references category_versions (category_id, version),
  check (gps_geom is null or is_sandbox or st_distance(geom, gps_geom) <= 15)
);
create index reports_geom_idx on reports using gist (geom);
create index reports_status_idx on reports (status, updated_at) where status not in ('clustered', 'failed');
create index reports_device_idx on reports (device_id, created_at desc);

create table report_photos (
  report_id     uuid not null references reports (id) on delete cascade,
  version       int not null check (version >= 1),
  photo_id      uuid not null unique references photos (id),
  capture_nonce text not null unique references capture_nonces (nonce),
  replaced_at   timestamptz,
  primary key (report_id, version)
);

create table cluster_events (
  id          bigint generated always as identity primary key,
  cluster_id  uuid not null references clusters (id) on delete cascade,
  from_status text,
  to_status   text not null,
  actor       text not null,
  at          timestamptz not null default now()
);

create table stakeholder_coverage (
  stakeholder_id uuid not null references stakeholders (id) on delete cascade,
  domain_id      text not null references domains (id),
  category_id    text references categories (id),
  primary key (stakeholder_id, domain_id)
);

create table dispatches (
  cluster_id     uuid not null,
  stakeholder_id uuid not null,
  is_sandbox     boolean not null default false,
  brief          jsonb,
  status         text not null default 'sent' check (status in ('sent', 'seen', 'accepted', 'declined', 'closed')),
  note           text,
  created_at     timestamptz not null default now(),
  seen_at        timestamptz,
  primary key (cluster_id, stakeholder_id),
  foreign key (cluster_id, is_sandbox) references clusters (id, is_sandbox) on delete cascade,
  foreign key (stakeholder_id, is_sandbox) references stakeholders (id, is_sandbox)
);

create table resolutions (
  id                  uuid primary key default gen_random_uuid(),
  is_sandbox          boolean not null default false,
  cluster_id          uuid not null,
  before_report_id    uuid references reports (id),
  photo_id            uuid not null unique references photos (id),
  capture_nonce       text unique references capture_nonces (nonce),
  closure_path        text not null check (closure_path in ('stakeholder', 'community')),
  submitted_by_user   uuid references profiles (user_id),
  submitted_by_device uuid references devices (id),
  stakeholder_id      uuid references stakeholders (id),
  geom                geography(point, 4326),
  verification        jsonb,
  ai_status           text not null default 'pending' check (ai_status in (
                        'pending',
                        'PERSISTENT_ISSUE',
                        'NOT_VERIFIABLE_REGION_NOT_VISIBLE',
                        'NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION',
                        'NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT',
                        'NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE',
                        'NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED',
                        'NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE',
                        'RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW')),
  outcome             text not null default 'open' check (outcome in ('open', 'resolved', 'rejected')),
  ai_overridden       boolean not null default false,
  decided_at          timestamptz,
  created_at          timestamptz not null default now(),
  foreign key (cluster_id, is_sandbox) references clusters (id, is_sandbox) on delete cascade,
  check ((closure_path = 'stakeholder') = (submitted_by_user is not null and stakeholder_id is not null)),
  check ((closure_path = 'community') = (submitted_by_device is not null))
);

create table resolution_reviews (
  id              uuid primary key default gen_random_uuid(),
  resolution_id   uuid not null references resolutions (id) on delete cascade,
  reviewer_user   uuid references profiles (user_id),
  reviewer_device uuid references devices (id),
  verdict         text not null check (verdict in ('approve', 'reject')),
  note            text,
  created_at      timestamptz not null default now(),
  check ((reviewer_user is null) <> (reviewer_device is null))
);

create table device_trust_events (
  id            bigint generated always as identity primary key,
  device_id     uuid not null references devices (id) on delete cascade,
  delta         real not null,
  trust_after   real not null,
  reason        text not null check (reason in ('report_confirmed', 'report_rejected', 'false_resolution_claim', 'community_review_agreed')),
  report_id     uuid references reports (id) on delete set null,
  resolution_id uuid references resolutions (id) on delete set null,
  created_at    timestamptz not null default now()
);

create table stage_runs (
  id            uuid primary key default gen_random_uuid(),
  report_id     uuid references reports (id) on delete cascade,
  photo_version int,
  resolution_id uuid references resolutions (id) on delete cascade,
  stage         text not null check (stage in ('geofence', 'phash', 'spec', 'sam_ground', 'classify', 'cluster', 'brief', 'closure')),
  status        text not null check (status in ('ok', 'error', 'skipped')),
  provider      text,
  model         text,
  latency_ms    int,
  error         text,
  started_at    timestamptz not null default now(),
  check ((report_id is null) <> (resolution_id is null))
);
create index stage_runs_report_idx on stage_runs (report_id, photo_version, stage);
create index stage_runs_resolution_idx on stage_runs (resolution_id, stage);

create table moderation_flags (
  id          uuid primary key default gen_random_uuid(),
  city_id     text not null references cities (id),
  report_id   uuid references reports (id) on delete cascade,
  cluster_id  uuid references clusters (id) on delete cascade,
  reason      text not null check (reason in ('reused_photo', 'impossible_gps', 'personal_data', 'low_trust_device', 'declined_by_actor')),
  source      text not null check (source in ('orchestrator', 'trigger')),
  evidence    jsonb,
  created_at  timestamptz not null default now(),
  resolved_by uuid references profiles (user_id),
  resolved_at timestamptz,
  resolution  text check (resolution in ('kept', 'hidden', 'restored', 'redispatched')),
  check ((report_id is null) <> (cluster_id is null))
);
create index moderation_flags_open_idx on moderation_flags (city_id, created_at) where resolved_at is null;

-- ---------------------------------------------------------------- housekeeping

create function touch_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger reports_touch before update on reports for each row execute function touch_updated_at();
create trigger categories_touch before update on categories for each row execute function touch_updated_at();
create trigger domains_touch before update on domains for each row execute function touch_updated_at();
