-- LLM providers, the model allowlist and usage (WP4b).
-- The core agents (documents, audio) keep using OpenAI models from the environment (CHAT_MODEL,
-- LIGHT_MODEL); this allowlist is for admin-built capabilities and, later, configurable agents and skills.
-- Models found by discovery start disabled. OpenRouter models may read at most `internal` data unless a
-- super_admin raises one. Usage records the tokens and cost of each call, never its content.
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.llm_providers (
  id uuid primary key default gen_random_uuid(),
  key text not null unique check (key in ('openai', 'openrouter')),
  name text not null,
  base_url text not null,
  -- The provider's API key, encrypted (connections.capability_id is null). OpenAI uses the environment key.
  connection_id uuid references @@schema@@.connections (id) on delete set null,
  enabled boolean not null default false,
  updated_at timestamptz,
  updated_by text
);

create table if not exists @@schema@@.llm_models (
  id uuid primary key default gen_random_uuid(),
  provider_id uuid not null references @@schema@@.llm_providers (id) on delete cascade,
  model_id text not null check (length(model_id) between 1 and 150),
  name text not null default '',
  enabled boolean not null default false,
  -- false = the provider no longer lists it (discovery); it can't be used
  available boolean not null default true,
  supports_tools boolean not null default false,
  supports_json_schema boolean not null default false,
  context_length integer,
  -- The most restricted data this model may read. Raising an OpenRouter model to 'sensitive' is super_admin-only.
  max_data_class text not null default 'internal' check (max_data_class in ('public', 'internal', 'sensitive')),
  -- OpenRouter provider preferences sent with every request (the request's "provider" object)
  routing jsonb not null default '{"data_collection": "deny"}'::jsonb check (jsonb_typeof(routing) = 'object'),
  price_in numeric check (price_in >= 0),    -- USD per million input tokens
  price_out numeric check (price_out >= 0),  -- USD per million output tokens
  discovered_at timestamptz,
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  updated_at timestamptz,
  updated_by text,
  unique (provider_id, model_id)
);

-- One row per LLM call. The model is stored as text ("openai:gpt-4o"), so calls to models that aren't in
-- the allowlist (the core agents) are counted too.
create table if not exists @@schema@@.llm_usage (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  user_id uuid references @@schema@@.users (id) on delete set null,
  provider text not null,
  model text not null,
  agent_key text,
  capability_key text,
  tokens_in integer not null default 0,
  tokens_out integer not null default 0,
  cost_usd numeric,          -- null when the price is unknown
  cost_source text check (cost_source in ('provider', 'price_list')),
  outcome text not null default 'ok' check (outcome in ('ok', 'error'))
);
create index if not exists llm_usage_at_idx on @@schema@@.llm_usage (at desc);
create index if not exists llm_usage_user_idx on @@schema@@.llm_usage (user_id, provider, at desc);

alter table @@schema@@.llm_providers enable row level security;
alter table @@schema@@.llm_models enable row level security;
alter table @@schema@@.llm_usage enable row level security;

-- Permissions name models as "<provider>:<model_id>". A deleted model takes its permissions with it.
create or replace function @@schema@@.llm_models_drop_permissions()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
declare
  provider_key text;
begin
  select key into provider_key from @@schema@@.llm_providers where id = old.provider_id;
  if provider_key is not null then
    delete from @@schema@@.permissions
    where resource_type = 'model' and resource_id = provider_key || ':' || old.model_id;
  end if;
  return old;
end;
$$;

drop trigger if exists llm_models_drop_permissions on @@schema@@.llm_models;
create trigger llm_models_drop_permissions after delete on @@schema@@.llm_models
  for each row execute function @@schema@@.llm_models_drop_permissions();

create or replace function @@schema@@.llm_models_keep_identity()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
begin
  if new.model_id is distinct from old.model_id or new.provider_id is distinct from old.provider_id then
    raise exception 'llm_models.provider_id and model_id cannot change (permissions refer to them)'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists llm_models_keep_identity on @@schema@@.llm_models;
create trigger llm_models_keep_identity before update of provider_id, model_id on @@schema@@.llm_models
  for each row execute function @@schema@@.llm_models_keep_identity();

revoke all on function @@schema@@.llm_models_drop_permissions() from public;
revoke all on function @@schema@@.llm_models_keep_identity() from public;

-- --- Seed -------------------------------------------------------------------------------------------------

insert into @@schema@@.llm_providers (key, name, base_url, enabled) values
  ('openai', 'OpenAI', 'https://api.openai.com/v1', true),
  ('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', false)
on conflict (key) do nothing;

insert into @@schema@@.permissions (subject_type, subject_id, resource_type, resource_id, action, effect)
select 'role', r.id, p.resource_type, p.resource_id, p.action, 'allow'
from (values
  ('admin', 'admin_area', 'models', 'admin')
) as p (role_key, resource_type, resource_id, action)
join @@schema@@.roles r on r.key = p.role_key
on conflict (subject_type, subject_id, resource_type, resource_id, action) do nothing;
