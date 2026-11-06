-- Admin panel: which call-recording folders are ingested (and the department of each), plus a small
-- key/value table the API and the indexer use to talk to each other (run requests, report, last run).
-- Idempotent: safe to re-run.

create table if not exists @@schema@@.ingestion_folders (
  name text primary key,
  enabled boolean not null default false,
  department text,
  updated_at timestamptz not null default now(),
  updated_by text
);
alter table @@schema@@.ingestion_folders enable row level security;

create table if not exists @@schema@@.ingestion_state (
  key text primary key,
  value jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table @@schema@@.ingestion_state enable row level security;

create index if not exists @@audio_table@@_audio_folder_idx
  on @@schema@@.@@audio_table@@ ((metadata->>'audio_folder'));

-- Relabels the stored calls of a folder when its department changes (no re-transcription needed).
-- Calls indexed before this migration have no audio_folder, so they are found by their path.
create or replace function @@schema@@.set_audio_department(folder text, dept text)
returns bigint
language plpgsql
set search_path = @@schema@@, public
as $$
declare
  updated bigint;
begin
  update @@schema@@.@@audio_table@@
  set metadata = case
    when dept is null then (metadata || jsonb_build_object('audio_folder', folder)) - 'department'
    else metadata || jsonb_build_object('audio_folder', folder, 'department', dept)
  end
  where metadata->>'audio_folder' = folder
     or position('/' || folder || '/' in coalesce(metadata->>'filepath', '')) > 0;
  get diagnostics updated = row_count;
  return updated;
end;
$$;
