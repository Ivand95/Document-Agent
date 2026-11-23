-- Capability registry: every tool, skill, CLI, MCP server and admin-built tool the agents may call,
-- the operations each one exposes (the whitelist), and the connections that hold their credentials.
-- Nothing runs unless it is registered, enabled, whitelisted, read-only and allowed by RBAC
-- (resource_type 'capability', resource_id = capabilities.key). Everything new starts disabled.
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.capabilities (
  id uuid primary key default gen_random_uuid(),
  -- What permissions name (permissions.resource_id). Never changes once created.
  key text not null unique check (key ~ '^[a-z][a-z0-9_]{1,62}$'),
  kind text not null check (kind in ('tool', 'skill', 'cli', 'mcp', 'prompt_tool', 'code_tool')),
  name text not null check (length(name) between 1 and 100),
  description text not null default '' check (length(description) <= 2000),
  enabled boolean not null default false,
  -- What the capability could do outside the app if its operations weren't restricted (shown in the panel).
  risk text not null default 'read' check (risk in ('read', 'write', 'financial')),
  -- What kind of data it returns: decides which LLM models may read its output (WP4b).
  data_class text not null default 'sensitive' check (data_class in ('public', 'internal', 'sensitive')),
  -- Non-secret settings (URL, executable, ...). Secrets live in connections, encrypted.
  config jsonb not null default '{}'::jsonb check (jsonb_typeof(config) = 'object'),
  version integer not null default 1 check (version >= 1),
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  updated_at timestamptz,
  updated_by text
);

-- The whitelist. Only operations listed here with enabled = true can be called, and only if read_only.
-- MCP tools found by discovery and operations of native tools are added disabled.
create table if not exists @@schema@@.capability_operations (
  capability_id uuid not null references @@schema@@.capabilities (id) on delete cascade,
  operation text not null check (operation ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
  description text not null default '' check (length(description) <= 2000),
  enabled boolean not null default false,
  -- true = changes nothing outside the app. The gateway never runs an operation marked false.
  read_only boolean not null default false,
  arg_schema jsonb not null default '{"type": "object"}'::jsonb check (jsonb_typeof(arg_schema) = 'object'),
  limits jsonb not null default '{}'::jsonb check (jsonb_typeof(limits) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz,
  updated_by text,
  primary key (capability_id, operation)
);

-- Credentials. secret_ciphertext is encrypted by the app (CAPABILITY_SECRET_KEY) and never returned by the
-- API. capability_id is null for connections that belong to no capability (e.g. an LLM provider key, WP4b).
create table if not exists @@schema@@.connections (
  id uuid primary key default gen_random_uuid(),
  capability_id uuid references @@schema@@.capabilities (id) on delete cascade,
  name text not null check (length(name) between 1 and 100),
  auth_type text not null check (auth_type in ('none', 'token', 'oauth', 'dsn')),
  config jsonb not null default '{}'::jsonb check (jsonb_typeof(config) = 'object'),
  scopes text[] not null default '{}',
  secret_ciphertext text,
  secret_updated_at timestamptz,
  status text not null default 'unchecked' check (status in ('unchecked', 'ok', 'error', 'disabled')),
  status_detail text check (length(status_detail) <= 500),
  last_check_at timestamptz,
  owner_user_id uuid references @@schema@@.users (id) on delete set null,
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  updated_at timestamptz,
  updated_by text
);
create unique index if not exists connections_capability_name_idx
  on @@schema@@.connections (coalesce(capability_id, '00000000-0000-0000-0000-000000000000'::uuid), name);

alter table @@schema@@.capabilities enable row level security;
alter table @@schema@@.capability_operations enable row level security;
alter table @@schema@@.connections enable row level security;

-- The key is what permissions point at: renaming it would silently move or orphan grants.
create or replace function @@schema@@.capabilities_keep_key()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
begin
  if new.key is distinct from old.key then
    raise exception 'capabilities.key cannot change (permissions refer to it)' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists capabilities_keep_key on @@schema@@.capabilities;
create trigger capabilities_keep_key before update of key on @@schema@@.capabilities
  for each row execute function @@schema@@.capabilities_keep_key();

-- A deleted capability takes its permissions with it, so a new capability with the same key doesn't
-- inherit old grants. (Deleting permissions bumps perm_version through the RBAC trigger.)
create or replace function @@schema@@.capabilities_drop_permissions()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
begin
  delete from @@schema@@.permissions where resource_type = 'capability' and resource_id = old.key;
  return old;
end;
$$;

drop trigger if exists capabilities_drop_permissions on @@schema@@.capabilities;
create trigger capabilities_drop_permissions after delete on @@schema@@.capabilities
  for each row execute function @@schema@@.capabilities_drop_permissions();

revoke all on function @@schema@@.capabilities_keep_key() from public;
revoke all on function @@schema@@.capabilities_drop_permissions() from public;

-- --- Seed: the admin role manages the registry (super_admin already has every admin area) ------------

insert into @@schema@@.permissions (subject_type, subject_id, resource_type, resource_id, action, effect)
select 'role', r.id, p.resource_type, p.resource_id, p.action, 'allow'
from (values
  ('admin', 'admin_area', 'capabilities', 'admin')
) as p (role_key, resource_type, resource_id, action)
join @@schema@@.roles r on r.key = p.role_key
on conflict (subject_type, subject_id, resource_type, resource_id, action) do nothing;
