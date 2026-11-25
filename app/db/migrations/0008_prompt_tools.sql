-- Prompt tools (WP4b): capabilities of kind 'prompt_tool' built by admins from a template, input and output
-- JSON schemas and an allowlisted model. They live in the capability registry (0006); this adds the result
-- of the last test run, which must be a success for the current version before the tool can be enabled,
-- and the admin area that builds them.
-- Idempotent: safe to re-run.

-- {"version": n, "ok": bool, "at": timestamp, "by": who, "error": code}: never the input or the output
alter table @@schema@@.capabilities add column if not exists last_test jsonb;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'capabilities_last_test_object' and conrelid = '@@schema@@.capabilities'::regclass
  ) then
    alter table @@schema@@.capabilities add constraint capabilities_last_test_object
      check (last_test is null or jsonb_typeof(last_test) = 'object');
  end if;
end;
$$;

-- --- Seed: admins and tool admins build prompt tools (super_admin already has every admin area) ---------

insert into @@schema@@.permissions (subject_type, subject_id, resource_type, resource_id, action, effect)
select 'role', r.id, p.resource_type, p.resource_id, p.action, 'allow'
from (values
  ('admin', 'admin_area', 'prompt_tools', 'admin'),
  ('tool_admin', 'admin_area', 'prompt_tools', 'admin')
) as p (role_key, resource_type, resource_id, action)
join @@schema@@.roles r on r.key = p.role_key
on conflict (subject_type, subject_id, resource_type, resource_id, action) do nothing;
