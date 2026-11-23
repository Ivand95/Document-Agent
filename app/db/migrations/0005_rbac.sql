-- Role-based access control: roles, who has them, what they allow, and an audit log.
-- Deny by default: a user can do something only if a permission allows it (explicit deny wins).
-- The seed at the end reproduces the rules that were hard-coded before (see rbac/legacy.py), so nobody
-- notices a change. Migrations run once (tracked), so the seed never brings back what an admin removed.
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.roles (
  id uuid primary key default gen_random_uuid(),
  key text not null unique check (key ~ '^[a-z][a-z0-9_]{1,62}$'),
  name text not null,
  description text,
  is_system boolean not null default false,  -- seeded roles: can't be deleted or renamed (key)
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  updated_at timestamptz,
  updated_by text
);

-- Roles given to a user by hand. Roles that follow from the department/position (role_mappings) are not
-- stored: they are worked out at every check, so they follow the user's current Microsoft 365 profile.
create table if not exists @@schema@@.user_roles (
  user_id uuid not null references @@schema@@.users (id) on delete cascade,
  role_id uuid not null references @@schema@@.roles (id) on delete cascade,
  granted_by text not null,
  granted_at timestamptz not null default now(),
  expires_at timestamptz,
  primary key (user_id, role_id)
);
create index if not exists user_roles_role_idx on @@schema@@.user_roles (role_id);

-- department: the user's department (accents and case ignored). position_prefix: the start of the
-- position, exact (e.g. "GR -" for managers). everyone: every active user.
create table if not exists @@schema@@.role_mappings (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('department', 'position_prefix', 'everyone')),
  value text not null default '',
  role_id uuid not null references @@schema@@.roles (id) on delete cascade,
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  unique (kind, value, role_id),
  check ((kind = 'everyone') = (value = ''))
);

-- resource_id '*' = every resource of that type. constraints: limits for later work packages
-- (e.g. {"row_limit": 5000}, {"llm_budget_usd_month": 20}).
create table if not exists @@schema@@.permissions (
  id uuid primary key default gen_random_uuid(),
  subject_type text not null check (subject_type in ('role', 'user')),
  subject_id uuid not null,
  resource_type text not null check (resource_type in ('capability', 'agent', 'admin_area', 'doc_category', 'model')),
  resource_id text not null check (length(resource_id) between 1 and 200),
  action text not null check (action in ('use', 'configure', 'admin')),
  effect text not null default 'allow' check (effect in ('allow', 'deny')),
  constraints jsonb not null default '{}'::jsonb check (jsonb_typeof(constraints) = 'object'),
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  updated_at timestamptz,
  updated_by text,
  unique (subject_type, subject_id, resource_type, resource_id, action)
);
create index if not exists permissions_subject_idx on @@schema@@.permissions (subject_type, subject_id);

-- Who changed what. Never the content of questions, answers or documents.
create table if not exists @@schema@@.audit_log (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  user_id uuid,          -- who did it (null for the system)
  actor text,            -- their email or name, kept even if the user is later removed
  action text not null,
  resource_type text,
  resource_id text,
  outcome text not null default 'ok',
  details jsonb not null default '{}'::jsonb
);
create index if not exists audit_log_at_idx on @@schema@@.audit_log (at desc);
create index if not exists audit_log_user_idx on @@schema@@.audit_log (user_id, at desc);
create index if not exists audit_log_resource_idx on @@schema@@.audit_log (resource_type, resource_id, at desc);

alter table @@schema@@.roles enable row level security;
alter table @@schema@@.user_roles enable row level security;
alter table @@schema@@.role_mappings enable row level security;
alter table @@schema@@.permissions enable row level security;
alter table @@schema@@.audit_log enable row level security;

-- Permissions given to a user directly, or roles given to them, change what that user may do:
-- bump their perm_version so the next token carries the new value. A change to a role's permissions
-- bumps the users who hold it by hand; users who get it from a mapping see it within the app's cache
-- time (RBAC_CACHE_SECONDS) anyway, because roles from mappings are worked out at every check.
create or replace function @@schema@@.rbac_bump_perm_version()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
declare
  v_pass text;
  v_type text;
  v_id uuid;
begin
  foreach v_pass in array array['old', 'new'] loop
    continue when (v_pass = 'old' and tg_op = 'INSERT') or (v_pass = 'new' and tg_op = 'DELETE');
    -- An update that keeps the subject bumps it once (in the 'new' pass)
    continue when v_pass = 'old' and tg_op = 'UPDATE'
      and to_jsonb(old) -> 'user_id' is not distinct from to_jsonb(new) -> 'user_id'
      and to_jsonb(old) -> 'subject_type' is not distinct from to_jsonb(new) -> 'subject_type'
      and to_jsonb(old) -> 'subject_id' is not distinct from to_jsonb(new) -> 'subject_id';
    if tg_table_name = 'user_roles' and v_pass = 'old' then
      v_type := 'user';
      v_id := old.user_id;
    elsif tg_table_name = 'user_roles' then
      v_type := 'user';
      v_id := new.user_id;
    elsif v_pass = 'old' then
      v_type := old.subject_type;
      v_id := old.subject_id;
    else
      v_type := new.subject_type;
      v_id := new.subject_id;
    end if;
    update @@schema@@.users u set perm_version = u.perm_version + 1
    where (v_type = 'user' and u.id = v_id)
       or (v_type = 'role' and u.id in (select ur.user_id from @@schema@@.user_roles ur where ur.role_id = v_id));
  end loop;
  return null;
end;
$$;

drop trigger if exists user_roles_perm_version on @@schema@@.user_roles;
create trigger user_roles_perm_version
  after insert or update or delete on @@schema@@.user_roles
  for each row execute function @@schema@@.rbac_bump_perm_version();

drop trigger if exists permissions_perm_version on @@schema@@.permissions;
create trigger permissions_perm_version
  after insert or update or delete on @@schema@@.permissions
  for each row execute function @@schema@@.rbac_bump_perm_version();

-- A permission row of a deleted user or role is meaningless: remove it with its subject
create or replace function @@schema@@.rbac_drop_subject_permissions()
returns trigger
language plpgsql
set search_path = @@schema@@, public
as $$
begin
  delete from @@schema@@.permissions p
  where p.subject_type = case when tg_table_name = 'roles' then 'role' else 'user' end
    and p.subject_id = old.id;
  return null;
end;
$$;

drop trigger if exists roles_drop_permissions on @@schema@@.roles;
create trigger roles_drop_permissions
  after delete on @@schema@@.roles
  for each row execute function @@schema@@.rbac_drop_subject_permissions();

drop trigger if exists users_drop_permissions on @@schema@@.users;
create trigger users_drop_permissions
  after delete on @@schema@@.users
  for each row execute function @@schema@@.rbac_drop_subject_permissions();

-- Trigger functions are not callable through the API, but PUBLIC gets execute on new functions by default
revoke all on function @@schema@@.rbac_bump_perm_version() from public;
revoke all on function @@schema@@.rbac_drop_subject_permissions() from public;

-- --- Seed: today's rules as data -------------------------------------------------------------------

insert into @@schema@@.roles (key, name, description, is_system) values
  ('super_admin', 'Super administrador', 'Todo, incluida la gestión de super administradores. Se asigna con: python -m rbac.cli grant-super-admin <email>', true),
  ('admin', 'Administrador', 'Panel de ingesta, usuarios, roles y permisos, auditoría (antes ADMIN_DEPARTMENTS)', true),
  ('manager', 'Gerencia', 'Puestos "GR -": todos los documentos y las llamadas', true),
  ('employee', 'Empleado', 'Todo usuario activo: agente de documentos y documentos generales', true),
  ('audio_user', 'Llamadas', 'Agente de llamadas (antes AUDIO_ALLOWED_DEPARTMENTS)', true),
  ('report_user', 'Reportes', 'Agente de reportes (cuando exista)', true),
  ('tool_admin', 'Administrador de herramientas', 'Escribe y aprueba herramientas de código (no las propias)', true),
  ('dept_gestion_humana', 'Gestión Humana', 'Documentos del departamento', true),
  ('dept_tecnologia', 'Tecnología', 'Documentos del departamento', true),
  ('dept_contabilidad', 'Contabilidad', 'Documentos del departamento', true),
  ('dept_administrativo', 'Administrativo', 'Documentos del departamento', true),
  ('dept_cumplimiento', 'Cumplimiento', 'Documentos del departamento', true),
  ('dept_marketing', 'Marketing', 'Documentos del departamento', true),
  ('dept_comercial', 'Comercial', 'Documentos del departamento', true),
  ('dept_creditos_cobros', 'Créditos y Cobros', 'Documentos del departamento', true),
  ('dept_atencion_socio', 'Atención al Socio', 'Documentos del departamento (categoría Comercial)', true),
  ('dept_auditoria', 'Auditoría', 'Documentos del departamento', true)
on conflict (key) do nothing;

insert into @@schema@@.role_mappings (kind, value, role_id)
select m.kind, m.value, r.id
from (values
  ('everyone', '', 'employee'),
  ('position_prefix', 'GR -', 'manager'),
  ('department', 'Tecnología', 'admin'),
  ('department', 'Gestion Humana', 'dept_gestion_humana'),
  ('department', 'Tecnología', 'dept_tecnologia'),
  ('department', 'Contabilidad', 'dept_contabilidad'),
  ('department', 'Administrativo', 'dept_administrativo'),
  ('department', 'Cumplimiento', 'dept_cumplimiento'),
  ('department', 'Marketing', 'dept_marketing'),
  ('department', 'Comercial', 'dept_comercial'),
  ('department', 'Creditos y Cobros', 'dept_creditos_cobros'),
  ('department', 'Atencion al Socio', 'dept_atencion_socio'),
  ('department', 'Auditoria', 'dept_auditoria')
) as m (kind, value, role_key)
join @@schema@@.roles r on r.key = m.role_key
on conflict (kind, value, role_id) do nothing;

insert into @@schema@@.permissions (subject_type, subject_id, resource_type, resource_id, action, effect)
select 'role', r.id, p.resource_type, p.resource_id, p.action, 'allow'
from (values
  -- super_admin: everything
  ('super_admin', 'capability', '*', 'use'), ('super_admin', 'capability', '*', 'configure'), ('super_admin', 'capability', '*', 'admin'),
  ('super_admin', 'agent', '*', 'use'), ('super_admin', 'agent', '*', 'configure'), ('super_admin', 'agent', '*', 'admin'),
  ('super_admin', 'admin_area', '*', 'use'), ('super_admin', 'admin_area', '*', 'configure'), ('super_admin', 'admin_area', '*', 'admin'),
  ('super_admin', 'doc_category', '*', 'use'), ('super_admin', 'doc_category', '*', 'configure'), ('super_admin', 'doc_category', '*', 'admin'),
  ('super_admin', 'model', '*', 'use'), ('super_admin', 'model', '*', 'configure'), ('super_admin', 'model', '*', 'admin'),
  -- admin: what ADMIN_DEPARTMENTS opened, plus the new user, role and audit screens
  ('admin', 'admin_area', 'ingestion', 'admin'),
  ('admin', 'admin_area', 'users', 'admin'),
  ('admin', 'admin_area', 'rbac', 'admin'),
  ('admin', 'admin_area', 'audit', 'admin'),
  -- managers ("GR -"): every document category and the calls
  ('manager', 'doc_category', 'HR', 'use'), ('manager', 'doc_category', 'IT', 'use'),
  ('manager', 'doc_category', 'FI', 'use'), ('manager', 'doc_category', 'ADM', 'use'),
  ('manager', 'doc_category', 'CU', 'use'), ('manager', 'doc_category', 'MKT', 'use'),
  ('manager', 'doc_category', 'COM', 'use'), ('manager', 'doc_category', 'CC', 'use'),
  ('manager', 'doc_category', 'AUD', 'use'), ('manager', 'doc_category', 'OTROS', 'use'),
  ('manager', 'doc_category', 'STAFF GERENCIAL', 'use'),
  ('manager', 'agent', 'audio', 'use'),
  -- everyone: the documents agent and the general documents
  ('employee', 'agent', 'documents', 'use'),
  ('employee', 'doc_category', 'OTROS', 'use'),
  ('audio_user', 'agent', 'audio', 'use'),
  ('tool_admin', 'admin_area', 'code_tools', 'admin'),
  -- one role per department: its own category
  ('dept_gestion_humana', 'doc_category', 'HR', 'use'),
  ('dept_tecnologia', 'doc_category', 'IT', 'use'),
  ('dept_contabilidad', 'doc_category', 'FI', 'use'),
  ('dept_administrativo', 'doc_category', 'ADM', 'use'),
  ('dept_cumplimiento', 'doc_category', 'CU', 'use'),
  ('dept_marketing', 'doc_category', 'MKT', 'use'),
  ('dept_comercial', 'doc_category', 'COM', 'use'),
  ('dept_creditos_cobros', 'doc_category', 'CC', 'use'),
  ('dept_atencion_socio', 'doc_category', 'COM', 'use'),
  ('dept_auditoria', 'doc_category', 'AUD', 'use')
) as p (role_key, resource_type, resource_id, action)
join @@schema@@.roles r on r.key = p.role_key
on conflict (subject_type, subject_id, resource_type, resource_id, action) do nothing;
