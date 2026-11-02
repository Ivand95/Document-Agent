-- Document search returns nothing unless the caller says which categories it may see.
-- Before, a NULL list meant "every category", so a missing filter would have exposed everything.
-- Same signature and return type as 0001, so callers don't change. Idempotent.

create or replace function @@schema@@.match_documents(
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
  begin
    perform set_config('hnsw.iterative_scan', 'strict_order', true);
  exception when others then
    null;
  end;

  return query
  select d.id, d.content, d.metadata,
         (1 - (d.embedding <=> query_embedding))::double precision
  from @@schema@@.@@docs_table@@ d
  where allowed_categories is not null
    and d.metadata->>'category' = any(allowed_categories)
    and 1 - (d.embedding <=> query_embedding) > match_threshold
  order by d.embedding <=> query_embedding
  limit match_count;
end;
$$;
