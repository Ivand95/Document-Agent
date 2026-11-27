-- CLI operations (WP6): the command line each whitelisted operation runs, as a template the app fills
-- from the validated arguments (capabilities/configs.py validate_command). Null for every other kind.
-- Idempotent: safe to re-run.

alter table @@schema@@.capability_operations add column if not exists command jsonb;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'capability_operations_command_array'
      and conrelid = '@@schema@@.capability_operations'::regclass
  ) then
    alter table @@schema@@.capability_operations add constraint capability_operations_command_array
      check (command is null or jsonb_typeof(command) = 'array');
  end if;
end;
$$;
