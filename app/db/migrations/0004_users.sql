-- Users, registered just in time by a successful Microsoft 365 login (there is no other way to create one).
-- Keyed by the Entra object id: the email can change, the object id doesn't.
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.users (
  id uuid primary key default gen_random_uuid(),
  entra_oid text not null unique,
  email text unique,                       -- lowercase; released if the address moves to another account
  name text,
  department text,
  position text,
  status text not null default 'active' check (status in ('active', 'pending', 'disabled')),
  perm_version integer not null default 1, -- bumped on any change to what the user may do (status, roles)
  first_login_at timestamptz not null default now(),
  last_login_at timestamptz not null default now(),
  created_by text not null default 'm365_login',
  updated_at timestamptz,
  updated_by text
);
alter table @@schema@@.users enable row level security;

create index if not exists users_status_idx on @@schema@@.users (status);

-- Called by the login callback. Creates the user on the first login (with the default status) or
-- refreshes the profile on later ones. The status of an existing user never changes here.
create or replace function @@schema@@.upsert_m365_user(
  p_oid text,
  p_email text,
  p_name text,
  p_department text,
  p_position text,
  p_default_status text
)
returns table (user_id uuid, user_status text, user_perm_version integer, created boolean)
language plpgsql
set search_path = @@schema@@, public
as $$
#variable_conflict use_column
declare
  v_email text := nullif(lower(btrim(p_email)), '');
begin
  if coalesce(btrim(p_oid), '') = '' then
    raise exception 'Entra object id is required';
  end if;
  if p_default_status not in ('active', 'pending') then
    raise exception 'Invalid default status %', p_default_status;
  end if;

  -- A mailbox reassigned to a different account: the old record keeps its history but loses the address
  if v_email is not null then
    update @@schema@@.users u
    set email = null, updated_at = now(), updated_by = 'm365_login'
    where u.email = v_email and u.entra_oid <> p_oid;
  end if;

  return query
  insert into @@schema@@.users as u (entra_oid, email, name, department, position, status)
  values (p_oid, v_email, nullif(btrim(p_name), ''), nullif(btrim(p_department), ''),
          nullif(btrim(p_position), ''), p_default_status)
  on conflict (entra_oid) do update
    set email = excluded.email,
        name = excluded.name,
        department = excluded.department,
        position = excluded.position,
        last_login_at = now()
  returning u.id, u.status, u.perm_version, (u.xmax = 0);
end;
$$;

-- Admin change of status. Bumps perm_version when the status really changes.
create or replace function @@schema@@.set_user_status(p_user_id uuid, p_status text, p_by text)
returns table (user_id uuid, user_status text, user_perm_version integer)
language plpgsql
set search_path = @@schema@@, public
as $$
#variable_conflict use_column
begin
  return query
  update @@schema@@.users u
  set perm_version = u.perm_version + case when u.status <> p_status then 1 else 0 end,
      status = p_status,
      updated_at = now(),
      updated_by = p_by
  where u.id = p_user_id
  returning u.id, u.status, u.perm_version;
end;
$$;

-- Only the backend (service_role, granted by db_setup) may register users or change their status.
-- Functions are executable by PUBLIC by default, which revoking anon/authenticated alone doesn't undo.
revoke all on function @@schema@@.upsert_m365_user(text, text, text, text, text, text) from public;
revoke all on function @@schema@@.set_user_status(uuid, text, text) from public;
