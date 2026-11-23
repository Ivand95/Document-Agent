-- Tables, indexes and search functions the agent needs. Idempotent: safe to re-run.
-- Names written between double at-signs are replaced by db_setup.py with validated identifiers.

-- 1. Tables (created only when missing; RLS on so the anon key can't read them)
do $$
begin
  if to_regclass('@@schema@@.@@docs_table@@') is null then
    create table @@schema@@.@@docs_table@@ (
      id bigint generated always as identity primary key,
      content text not null,
      metadata jsonb not null default '{}'::jsonb,
      embedding @@vector_schema@@.vector(@@dim@@) not null
    );
    alter table @@schema@@.@@docs_table@@ enable row level security;
  end if;

  if to_regclass('@@schema@@.@@audio_table@@') is null then
    create table @@schema@@.@@audio_table@@ (
      id bigint generated always as identity primary key,
      content text not null,
      metadata jsonb not null default '{}'::jsonb,
      embedding @@vector_schema@@.vector(@@dim@@) not null
    );
    alter table @@schema@@.@@audio_table@@ enable row level security;
  end if;
end $$;

-- 2. Indexes
-- Approximate nearest neighbour search
create index if not exists @@docs_table@@_embedding_hnsw
  on @@schema@@.@@docs_table@@ using hnsw (embedding @@vector_schema@@.vector_cosine_ops);
create index if not exists @@audio_table@@_embedding_hnsw
  on @@schema@@.@@audio_table@@ using hnsw (embedding @@vector_schema@@.vector_cosine_ops);

-- delete-by-file on every re-index, and the category filter of every question
create index if not exists @@docs_table@@_filepath_idx
  on @@schema@@.@@docs_table@@ ((metadata->>'filepath'));
create index if not exists @@docs_table@@_category_idx
  on @@schema@@.@@docs_table@@ ((metadata->>'category'));

-- delete-by-file and the extension / date / recipient filters of call questions
create index if not exists @@audio_table@@_filepath_idx
  on @@schema@@.@@audio_table@@ ((metadata->>'filepath'));
create index if not exists @@audio_table@@_extension_idx
  on @@schema@@.@@audio_table@@ ((metadata->>'extension'));
create index if not exists @@audio_table@@_date_idx
  on @@schema@@.@@audio_table@@ ((metadata->>'date_formatted'));
create index if not exists @@audio_table@@_phone_idx
  on @@schema@@.@@audio_table@@ ((metadata->>'phone_number'));

-- 3. Search functions (called through PostgREST rpc)
-- Dropped first: an older version with another return type can't be replaced in place.
drop function if exists @@schema@@.match_documents(@@vector_schema@@.vector, double precision, integer, text[]);

create function @@schema@@.match_documents(
  query_embedding @@vector_schema@@.vector(@@dim@@),
  match_threshold double precision default 0.5,
  match_count integer default 10,
  allowed_categories text[] default null
)
returns table (id bigint, content text, metadata jsonb, similarity double precision)
language plpgsql
stable
set search_path = @@schema@@, @@vector_schema@@, public
as $$
begin
  -- Keep scanning the HNSW index until enough rows pass the category filter (pgvector >= 0.8)
  begin
    perform set_config('hnsw.iterative_scan', 'strict_order', true);
  exception when others then
    null;
  end;

  return query
  select d.id, d.content, d.metadata,
         (1 - (d.embedding <=> query_embedding))::double precision
  from @@schema@@.@@docs_table@@ d
  where (allowed_categories is null or d.metadata->>'category' = any(allowed_categories))
    and 1 - (d.embedding <=> query_embedding) > match_threshold
  order by d.embedding <=> query_embedding
  limit match_count;
end;
$$;

drop function if exists @@schema@@.match_conversations(@@vector_schema@@.vector, double precision, integer, text, text, text);

create function @@schema@@.match_conversations(
  query_embedding @@vector_schema@@.vector(@@dim@@),
  match_threshold double precision default 0.2,
  match_count integer default 10,
  filter_extension text default null,
  filter_date text default null,
  filter_recipient text default null
)
returns table (id bigint, content text, metadata jsonb, similarity double precision)
language plpgsql
stable
set search_path = @@schema@@, @@vector_schema@@, public
as $$
begin
  begin
    perform set_config('hnsw.iterative_scan', 'strict_order', true);
  exception when others then
    null;
  end;

  return query
  select d.id, d.content, d.metadata,
         (1 - (d.embedding <=> query_embedding))::double precision
  from @@schema@@.@@audio_table@@ d
  where (filter_extension is null or d.metadata->>'extension' = filter_extension)
    and (filter_date is null or d.metadata->>'date_formatted' = filter_date)
    and (filter_recipient is null or d.metadata->>'phone_number' = filter_recipient)
    and 1 - (d.embedding <=> query_embedding) > match_threshold
  order by d.embedding <=> query_embedding
  limit match_count;
end;
$$;
