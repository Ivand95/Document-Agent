-- Roles a department admin may assign or remove from the users of their departments
-- (/admin/users/{id}/roles). Admins with the rbac area manage every role. The app also refuses, at
-- assignment time, a manager-assignable role whose permissions open an admin area or grant a whole
-- resource type ("*"), so marking such a role by mistake delegates nothing dangerous.
-- Idempotent: safe to re-run (the seed runs only when the column is created, so admins' changes stay).

do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = '@@schema@@' and table_name = 'roles' and column_name = 'manager_assignable'
  ) then
    alter table @@schema@@.roles add column manager_assignable boolean not null default false;
    -- Seed: access to the calls and reports agents
    update @@schema@@.roles set manager_assignable = true where key in ('audio_user', 'report_user');
  end if;
end;
$$;
