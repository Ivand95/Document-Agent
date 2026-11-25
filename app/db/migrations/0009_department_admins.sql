-- Department admins ("gerentes" in the admin panel): users who manage the users of their own department
-- (their Microsoft 365 department, which follows their profile) and of the extra departments assigned to
-- them here. They get the Usuarios admin area through the department_admin role, whose permission carries
-- {"scope": "departments"}. A grant of the area without that constraint (admin, super_admin) is not
-- limited: constraints only survive when every matching rule has them.
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.user_admin_departments (
  user_id uuid not null references @@schema@@.users (id) on delete cascade,
  -- Spelled as in the users' Microsoft 365 profiles; compared without accents or case
  department text not null check (length(btrim(department)) between 1 and 100),
  created_at timestamptz not null default now(),
  created_by text not null default 'system',
  primary key (user_id, department)
);
alter table @@schema@@.user_admin_departments enable row level security;

-- --- Seed -------------------------------------------------------------------------------------------------

insert into @@schema@@.roles (key, name, description, is_system) values
  ('department_admin', 'Administrador de departamento', 'Usuarios de su departamento y de los departamentos que se le asignen', true)
on conflict (key) do nothing;

insert into @@schema@@.permissions (subject_type, subject_id, resource_type, resource_id, action, effect, constraints)
select 'role', r.id, p.resource_type, p.resource_id, p.action, 'allow', p.constraints::jsonb
from (values
  ('department_admin', 'admin_area', 'users', 'admin', '{"scope": "departments"}')
) as p (role_key, resource_type, resource_id, action, constraints)
join @@schema@@.roles r on r.key = p.role_key
on conflict (subject_type, subject_id, resource_type, resource_id, action) do nothing;
