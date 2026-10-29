"""Creates (and checks) everything Supabase needs for the agent's searches.

    uv run python app/db_setup.py --check       # what the app sees through the REST API
    uv run python app/db_setup.py --apply       # create tables, indexes and functions
    uv run python app/db_setup.py --print-sql   # the SQL, to paste in the Supabase SQL editor

--apply needs SUPABASE_DB_URL, a Postgres connection string the container can reach: the db service of
a self-hosted Supabase (user postgres), or Supavisor in session mode (port 5432), or the cloud session pooler.
The API and the indexer call ensure_database() on startup, so with that variable set a fresh
database is prepared automatically.
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

from config import (
    SUPABASE_AUDIO_TABLE,
    SUPABASE_SCHEMA,
    SUPABASE_TABLE,
    global_supabase_client,
)

log = logging.getLogger("db_setup")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
EMBEDDING_DIM = 1536  # text-embedding-3-small
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
LOCK_KEY = "document_agent_migrations"
TRACKING_TABLE = "document_agent_migrations"  # own name: the schema may be `public`, shared with other apps

# Roles that may use the tables and functions. The backend must use the service_role key.
GRANT_ROLES = [r.strip() for r in os.getenv("DB_GRANT_ROLES", "service_role").split(",") if r.strip()]
# Roles that must NOT call the search functions (Supabase grants them by default in public)
REVOKE_ROLES = ("anon", "authenticated")


def identifiers():
    names = {"schema": SUPABASE_SCHEMA, "docs_table": SUPABASE_TABLE, "audio_table": SUPABASE_AUDIO_TABLE}
    for key, value in names.items():
        if not IDENTIFIER.match(value or ""):
            raise ValueError(f"{key} '{value}' must be lowercase letters, digits and underscores")
    return names


def render(sql, vector_schema):
    """Fills the @@placeholders@@; identifiers are validated, so plain replacement is safe."""
    values = {**identifiers(), "vector_schema": vector_schema, "dim": str(EMBEDDING_DIM)}
    for key, value in values.items():
        sql = sql.replace(f"@@{key}@@", value)
    return sql


def migration_files():
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


# --- Applying -------------------------------------------------------------------------------
def ensure_extension(conn):
    """Schema where pgvector lives, installing it if needed (Supabase convention: `extensions`)."""
    from psycopg import sql

    row = conn.execute(
        "select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace "
        "where e.extname = 'vector'"
    ).fetchone()
    if row:
        return row[0]
    has_extensions = conn.execute("select 1 from pg_namespace where nspname = 'extensions'").fetchone()
    target = "extensions" if has_extensions else "public"
    conn.execute(sql.SQL("create extension vector with schema {}").format(sql.Identifier(target)))
    return target


def role_exists(conn, role):
    return conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone() is not None


def apply_grants(conn, names):
    from psycopg import sql

    schema, docs, audio = names["schema"], names["docs_table"], names["audio_table"]
    tables = sql.SQL(", ").join(sql.Identifier(schema, t) for t in (docs, audio))
    functions = [
        sql.SQL("{}.match_documents(vector, double precision, integer, text[])").format(sql.Identifier(schema)),
        sql.SQL("{}.match_conversations(vector, double precision, integer, text, text, text)").format(
            sql.Identifier(schema)
        ),
    ]
    # `vector` must resolve in the search path used for the grant statements
    conn.execute(sql.SQL("set local search_path = {}, {}, public").format(
        sql.Identifier(schema), sql.Identifier(ensure_extension(conn))))

    for role in GRANT_ROLES:
        if not role_exists(conn, role):
            log.warning("Role '%s' does not exist; skipping its grants", role)
            continue
        r = sql.Identifier(role)
        conn.execute(sql.SQL("grant usage on schema {} to {}").format(sql.Identifier(schema), r))
        conn.execute(sql.SQL("grant all on table {} to {}").format(tables, r))
        for fn in functions:
            conn.execute(sql.SQL("grant execute on function {} to {}").format(fn, r))

    for role in REVOKE_ROLES:
        if role in GRANT_ROLES or not role_exists(conn, role):
            continue
        for fn in functions:
            conn.execute(sql.SQL("revoke execute on function {} from {}").format(fn, sql.Identifier(role)))


def ensure_schema_exposed(conn, schema):
    """PostgREST only serves schemas listed in pgrst.db_schemas. Returns True if it was changed."""
    from psycopg import sql

    if schema == "public":
        return False
    row = conn.execute("select rolconfig from pg_roles where rolname = 'authenticator'").fetchone()
    config = next(
        (c.split("=", 1)[1] for c in (row[0] or []) if c.startswith("pgrst.db_schemas=")) if row else [],
        None,
    )
    if config is None:
        # Self-hosted: schemas come from PGRST_DB_SCHEMAS on the PostgREST container, not from the database
        log.warning(
            "Cannot tell if schema '%s' is exposed. Self-hosted: add it to PGRST_DB_SCHEMAS of the rest service "
            "(or use SUPABASE_SCHEMA=public). Cloud: Settings > API > Exposed schemas.", schema)
        return False
    exposed = [s.strip() for s in config.split(",") if s.strip()]
    if schema in exposed:
        return False
    value = ", ".join(exposed + [schema])
    try:
        conn.execute(sql.SQL("alter role authenticator set pgrst.db_schemas = {}").format(sql.Literal(value)))
    except Exception as e:
        log.warning("Could not expose schema '%s' (%s); add it to PGRST_DB_SCHEMAS (self-hosted) or "
                    "Settings > API > Exposed schemas (cloud)", schema, e)
        return False
    return True


def apply_migrations(dsn):
    """Runs pending migrations. Returns the versions applied now. Safe to call concurrently."""
    import psycopg
    from psycopg import sql

    names = identifiers()
    schema = names["schema"]
    applied_now = []

    # prepare_threshold=None: session/transaction poolers don't support prepared statements
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
        try:
            vector_schema = ensure_extension(conn)
            conn.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(schema)))
            tracking = sql.Identifier(schema, TRACKING_TABLE)
            conn.execute(
                sql.SQL("create table if not exists {} (version text primary key, applied_at timestamptz not null default now())")
                .format(tracking)
            )
            done = {r[0] for r in conn.execute(sql.SQL("select version from {}").format(tracking))}

            for path in migration_files():
                if path.stem in done:
                    continue
                with conn.transaction():
                    conn.execute(render(path.read_text(), vector_schema))
                    conn.execute(sql.SQL("insert into {} (version) values (%s)").format(tracking), (path.stem,))
                applied_now.append(path.stem)
                log.info("Applied migration %s", path.stem)

            with conn.transaction():
                apply_grants(conn, names)
            exposure_changed = ensure_schema_exposed(conn, schema)

            # Make PostgREST see the new functions (and the schema list, if it changed)
            conn.execute("notify pgrst, 'reload schema'")
            if exposure_changed:
                conn.execute("notify pgrst, 'reload config'")
        finally:
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))
    return applied_now


# --- Checking -------------------------------------------------------------------------------
def explain(what, error):
    code = getattr(error, "code", None)
    message = getattr(error, "message", None) or str(error)
    hints = {
        "PGRST202": "the function does not exist in this schema",
        "PGRST205": "the table does not exist in this schema",
        "PGRST106": f"schema '{SUPABASE_SCHEMA}' is not exposed: add it to PGRST_DB_SCHEMAS on the rest service "
                    "(self-hosted; default is public,storage,graphql_public) or use SUPABASE_SCHEMA=public",
        "42501": "permission denied: SUPABASE_KEY must be the service_role key",
    }
    hint = hints.get(code, "unexpected error")
    return f"{what} [{code}] {hint}: {message}"


def check_search_ready(client=global_supabase_client):
    """Calls what the agent calls (through the REST API). Returns a list of problems; empty = ready."""
    from postgrest.exceptions import APIError

    schema = client.schema(SUPABASE_SCHEMA)
    vector = [0.001] * EMBEDDING_DIM
    checks = {
        f"table {SUPABASE_SCHEMA}.{SUPABASE_TABLE}": lambda: schema.table(SUPABASE_TABLE).select("id").limit(1).execute(),
        f"table {SUPABASE_SCHEMA}.{SUPABASE_AUDIO_TABLE}": lambda: schema.table(SUPABASE_AUDIO_TABLE).select("id").limit(1).execute(),
        f"function {SUPABASE_SCHEMA}.match_documents": lambda: schema.rpc(
            "match_documents",
            {"query_embedding": vector, "match_threshold": 0.99, "match_count": 1, "allowed_categories": ["OTROS"]},
        ).execute(),
        f"function {SUPABASE_SCHEMA}.match_conversations": lambda: schema.rpc(
            "match_conversations",
            {"query_embedding": vector, "match_threshold": 0.99, "match_count": 1,
             "filter_extension": None, "filter_date": None, "filter_recipient": None},
        ).execute(),
    }
    problems = []
    for what, call in checks.items():
        try:
            call()
        except APIError as e:
            problems.append(explain(what, e))
        except Exception as e:  # network, bad URL...
            problems.append(f"{what}: {e}")
    return problems


def ensure_database():
    """Applies migrations when SUPABASE_DB_URL is set, then reports anything still missing.
    Never raises: a database problem must not stop the API from booting."""
    dsn = os.getenv("SUPABASE_DB_URL")
    if dsn and os.getenv("DB_AUTO_MIGRATE", "true").lower() == "true":
        try:
            applied = apply_migrations(dsn)
            log.info("Database ready (new migrations: %s)", ", ".join(applied) or "none")
        except Exception:
            log.exception("Database setup failed")
    problems = check_search_ready()
    for problem in problems:
        log.error("Search not ready: %s", problem)
    if problems and not dsn:
        log.error("Set SUPABASE_DB_URL to create it automatically, or run: uv run python app/db_setup.py --print-sql")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--print-sql", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.print_sql:
        # Standard Supabase layout: pgvector in the `extensions` schema
        print("create extension if not exists vector with schema extensions;")
        print(f"create schema if not exists {SUPABASE_SCHEMA};\n")
        for path in migration_files():
            print(f"-- {path.name}\n{render(path.read_text(), 'extensions')}")
        print(f"grant usage on schema {SUPABASE_SCHEMA} to service_role;")
        print(f"grant all on table {SUPABASE_SCHEMA}.{SUPABASE_TABLE}, {SUPABASE_SCHEMA}.{SUPABASE_AUDIO_TABLE} to service_role;")
        print("grant execute on all functions in schema " + SUPABASE_SCHEMA + " to service_role;")
        if SUPABASE_SCHEMA != "public":
            print("-- and expose the schema: PGRST_DB_SCHEMAS on the rest service (self-hosted) or Settings > API (cloud)")
        print("notify pgrst, 'reload schema';")
    elif args.apply:
        dsn = os.getenv("SUPABASE_DB_URL")
        if not dsn:
            sys.exit("SUPABASE_DB_URL is not set (Supabase > Connect > Session pooler)")
        applied = apply_migrations(dsn)
        print("Applied:", ", ".join(applied) or "nothing new")
    if not args.print_sql:
        problems = check_search_ready()
        print("Search is ready." if not problems else "\n".join(f"- {p}" for p in problems))
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
