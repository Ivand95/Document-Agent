import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import db_setup  # noqa: E402

DIM = db_setup.EMBEDDING_DIM


def unit_vector(i):
    """Orthogonal vectors: identical -> similarity 1, different -> 0."""
    return "[" + ",".join("1" if j == i else "0" for j in range(DIM)) + "]"


class RenderTest(unittest.TestCase):
    def test_placeholders_are_all_replaced(self):
        for path in db_setup.migration_files():
            sql = db_setup.render(path.read_text(), "extensions")
            self.assertNotIn("@@", sql, path.name)
        self.assertIn("extensions.vector(1536)", sql)

    def test_unsafe_identifiers_are_rejected(self):
        for bad in ('docs"; drop table x; --', "Docs", "1abc", "a" * 64, ""):
            with mock.patch.object(db_setup, "SUPABASE_SCHEMA", bad):
                with self.assertRaises(ValueError, msg=bad):
                    db_setup.identifiers()

    def test_migrations_are_versioned_in_order(self):
        names = [p.stem for p in db_setup.migration_files()]
        self.assertEqual(names, sorted(names))
        self.assertTrue(names[0].startswith("0001_"))


class CheckTest(unittest.TestCase):
    def api_error(self, code):
        from postgrest.exceptions import APIError

        return APIError({"message": "boom", "code": code, "hint": None, "details": None})

    def client(self, failing=None):
        """Fake supabase client: every call succeeds unless `failing` maps a name to an error."""
        failing = failing or {}

        class Call:
            def __init__(self, name):
                self.name = name

            def __getattr__(self, _):
                return lambda *a, **k: self

            def execute(self):
                if self.name in failing:
                    raise failing[self.name]

        class Schema:
            def table(self, name):
                return Call(name)

            def rpc(self, name, params):
                return Call(name)

        return mock.Mock(schema=lambda _: Schema())

    def test_ready_when_everything_answers(self):
        self.assertEqual(db_setup.check_search_ready(self.client()), [])

    def test_missing_function_is_explained(self):
        problems = db_setup.check_search_ready(self.client({"match_documents": self.api_error("PGRST202")}))
        self.assertEqual(len(problems), 1)
        self.assertIn("match_documents", problems[0])
        self.assertIn("does not exist", problems[0])

    def test_each_error_code_has_an_actionable_hint(self):
        for code, text in (("PGRST106", "PGRST_DB_SCHEMAS"), ("42501", "service_role"), ("PGRST205", "table")):
            problems = db_setup.check_search_ready(self.client({db_setup.SUPABASE_TABLE: self.api_error(code)}))
            self.assertIn(text, problems[0], code)


class EnsureDatabaseTest(unittest.TestCase):
    def test_without_db_url_it_only_checks(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_DB_URL", None)
            with mock.patch.object(db_setup, "apply_migrations") as apply, \
                 mock.patch.object(db_setup, "check_search_ready", return_value=["x"]):
                self.assertEqual(db_setup.ensure_database(), ["x"])
        apply.assert_not_called()

    def test_migration_failure_never_raises(self):
        with mock.patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://nope"}):
            with mock.patch.object(db_setup, "apply_migrations", side_effect=RuntimeError("down")), \
                 mock.patch.object(db_setup, "check_search_ready", return_value=[]):
                self.assertEqual(db_setup.ensure_database(), [])

    def test_auto_migrate_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"SUPABASE_DB_URL": "postgresql://x", "DB_AUTO_MIGRATE": "false"}):
            with mock.patch.object(db_setup, "apply_migrations") as apply, \
                 mock.patch.object(db_setup, "check_search_ready", return_value=[]):
                db_setup.ensure_database()
        apply.assert_not_called()


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to a throwaway Postgres with pgvector")
class PostgresIntegrationTest(unittest.TestCase):
    """Runs the real migration. Creates and drops its own database on the server."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.admin_dsn = os.environ["TEST_DATABASE_URL"]
        with psycopg.connect(cls.admin_dsn, autocommit=True) as c:
            for role, extra in (("service_role", "bypassrls"), ("anon", ""), ("authenticated", ""), ("authenticator", "")):
                if not c.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                    c.execute(f"create role {role} nologin {extra}")

    def setUp(self):
        import psycopg
        from psycopg import conninfo

        self.db = f"da_test_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            c.execute(f"create database {self.db}")
        self.dsn = conninfo.make_conninfo(self.admin_dsn, dbname=self.db)
        self.addCleanup(self.drop_db)
        self.schema, self.docs, self.audio = db_setup.SUPABASE_SCHEMA, db_setup.SUPABASE_TABLE, db_setup.SUPABASE_AUDIO_TABLE

    def drop_db(self):
        import psycopg

        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            c.execute(f"drop database if exists {self.db} with (force)")

    def connect(self):
        import psycopg

        return psycopg.connect(self.dsn, autocommit=True)

    def fill(self, conn):
        rows = [
            ("vacaciones", {"category": "HR", "filepath": "a"}, 0),
            ("soporte", {"category": "IT", "filepath": "b"}, 1),
            ("general", {"category": "OTROS", "filepath": "c"}, 0),
        ]
        for content, meta, i in rows:
            conn.execute(
                f"insert into {self.schema}.{self.docs} (content, metadata, embedding) values (%s, %s::jsonb, %s::vector)",
                (content, __import__("json").dumps(meta), unit_vector(i)),
            )
        calls = [
            ("hola", {"extension": "1055", "date_formatted": "2026-05-08", "phone_number": "8091111111"}, 0),
            ("adios", {"extension": "1034", "date_formatted": "2026-05-09", "phone_number": "8092222222"}, 0),
        ]
        for content, meta, i in calls:
            conn.execute(
                f"insert into {self.schema}.{self.audio} (content, metadata, embedding) values (%s, %s::jsonb, %s::vector)",
                (content, __import__("json").dumps(meta), unit_vector(i)),
            )

    def search(self, conn, query, threshold=0.5, count=10, categories=None):
        return conn.execute(
            f"select content, similarity from {self.schema}.match_documents(%s::vector, %s, %s, %s::text[]) order by similarity desc, content",
            (unit_vector(query), threshold, count, categories),
        ).fetchall()

    def test_fresh_database_gets_tables_functions_and_working_search(self):
        applied = db_setup.apply_migrations(self.dsn)
        self.assertEqual(applied, ["0001_vector_search"])

        with self.connect() as c:
            self.fill(c)
            hits = self.search(c, 0)
            self.assertEqual([h[0] for h in hits], ["general", "vacaciones"])  # IT row is orthogonal
            self.assertAlmostEqual(hits[0][1], 1.0, places=5)

            self.assertEqual([h[0] for h in self.search(c, 0, categories=["HR"])], ["vacaciones"])
            self.assertEqual(self.search(c, 0, categories=["FI"]), [])
            self.assertEqual(len(self.search(c, 0, count=1)), 1)
            self.assertEqual(self.search(c, 0, threshold=1.5), [])
            self.assertEqual({h[0] for h in self.search(c, 0, threshold=-1)}, {"vacaciones", "soporte", "general"})

            conv = lambda **f: c.execute(  # noqa: E731
                f"select content from {self.schema}.match_conversations(%s::vector, 0.2, 10, %s, %s, %s) order by content",
                (unit_vector(0), f.get("ext"), f.get("date"), f.get("phone")),
            ).fetchall()
            self.assertEqual(conv(), [("adios",), ("hola",)])
            self.assertEqual(conv(ext="1055"), [("hola",)])
            self.assertEqual(conv(date="2026-05-09"), [("adios",)])
            self.assertEqual(conv(phone="8091111111"), [("hola",)])
            self.assertEqual(conv(ext="1055", date="2026-05-09"), [])

    def test_search_functions_work_with_default_arguments(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            self.fill(c)
            rows = c.execute(
                f"select content from {self.schema}.match_documents(%s::vector)", (unit_vector(0),)
            ).fetchall()
            self.assertEqual({r[0] for r in rows}, {"vacaciones", "general"})

    def test_indexes_exist_and_are_used_by_delete_by_file(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            indexes = {r[0] for r in c.execute("select indexname from pg_indexes where schemaname=%s", (self.schema,))}
            for expected in (f"{self.docs}_embedding_hnsw", f"{self.docs}_filepath_idx", f"{self.docs}_category_idx",
                             f"{self.audio}_embedding_hnsw", f"{self.audio}_extension_idx", f"{self.audio}_phone_idx"):
                self.assertIn(expected, indexes)
            c.execute("set enable_seqscan = off")  # tiny table: force the planner to show index usage
            plan = "\n".join(r[0] for r in c.execute(
                f"explain delete from {self.schema}.{self.docs} where metadata->>'filepath' = 'x'"))
            self.assertIn(f"{self.docs}_filepath_idx", plan)

    def test_rerun_is_a_noop_and_keeps_data(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            self.fill(c)
        self.assertEqual(db_setup.apply_migrations(self.dsn), [])
        with self.connect() as c:
            self.assertEqual(c.execute(f"select count(*) from {self.schema}.{self.docs}").fetchone()[0], 3)

    def test_existing_tables_and_old_functions_are_adopted(self):
        with self.connect() as c:
            c.execute("create extension vector")
            c.execute(f"create schema {self.schema}")
            c.execute(f"create table {self.schema}.{self.docs} (id bigserial primary key, content text, metadata jsonb, embedding vector(1536))")
            c.execute(f"insert into {self.schema}.{self.docs} (content, metadata, embedding) values ('viejo', '{{\"category\":\"HR\"}}', '{unit_vector(0)}')")
            # an older function with another return type can't be replaced in place
            c.execute(f"create function {self.schema}.match_documents(query_embedding vector, match_threshold double precision, match_count integer, allowed_categories text[]) returns table (content text) language sql as $$ select 'legacy'::text $$")
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            self.assertEqual([h[0] for h in self.search(c, 0)], ["viejo"])

    def test_pgvector_in_the_extensions_schema(self):
        with self.connect() as c:
            c.execute("create schema extensions")
            c.execute("create extension vector with schema extensions")
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set search_path = public, extensions")
            self.fill(c)
            self.assertEqual(len(self.search(c, 0)), 2)

    def test_only_the_service_role_can_search(self):
        db_setup.apply_migrations(self.dsn)
        import psycopg

        with self.connect() as c:
            self.fill(c)
            c.execute("set role service_role")
            self.assertEqual(len(self.search(c, 0)), 2)
            c.execute("reset role")
            for role in ("anon", "authenticated"):
                c.execute(f"set role {role}")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=role):
                    self.search(c, 0)
                c.execute("reset role")

    def test_tables_are_not_readable_by_anon(self):
        db_setup.apply_migrations(self.dsn)
        import psycopg

        with self.connect() as c:
            self.fill(c)
            c.execute("set role anon")
            try:
                rows = c.execute(f"select count(*) from {self.schema}.{self.docs}").fetchone()[0]
                self.assertEqual(rows, 0)  # RLS hides every row
            except psycopg.errors.InsufficientPrivilege:
                pass  # or no table privileges at all: both are fine

    def use_schema(self, schema):
        for name in ("SUPABASE_SCHEMA",):
            patcher = mock.patch.object(db_setup, name, schema)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.schema = schema

    def test_public_schema_as_used_by_self_hosted(self):
        self.use_schema("public")
        with self.connect() as c:  # another app already owns a table and a migrations-like table in public
            c.execute("create table public.schema_migrations (version text)")
        self.assertEqual(db_setup.apply_migrations(self.dsn), ["0001_vector_search"])
        with self.connect() as c:
            self.fill(c)
            self.assertEqual([h[0] for h in self.search(c, 0, categories=["HR"])], ["vacaciones"])
            tracked = c.execute(f"select version from public.{db_setup.TRACKING_TABLE}").fetchall()
            self.assertEqual(tracked, [("0001_vector_search",)])
            c.execute("set role service_role")
            self.assertEqual(len(self.search(c, 0)), 2)

    def test_schema_is_exposed_when_postgrest_reads_its_config_from_the_database(self):
        import psycopg

        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute("alter role authenticator set pgrst.db_schemas = 'public, storage'")
            self.addCleanup(lambda: psycopg.connect(self.admin_dsn, autocommit=True).execute(
                "alter role authenticator reset pgrst.db_schemas"))
        db_setup.apply_migrations(self.dsn)
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            config = admin.execute("select rolconfig from pg_roles where rolname = 'authenticator'").fetchone()[0]
        self.assertIn(f"pgrst.db_schemas=public, storage, {self.schema}", config)
        db_setup.apply_migrations(self.dsn)  # already listed: not added twice
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            config = admin.execute("select rolconfig from pg_roles where rolname = 'authenticator'").fetchone()[0]
        self.assertEqual(str(config).count(self.schema), 1)

    def test_selfhosted_without_db_config_leaves_postgrest_settings_alone(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)  # no pgrst.db_schemas on the role: only warns
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            config = admin.execute("select rolconfig from pg_roles where rolname = 'authenticator'").fetchone()[0]
        self.assertFalse(any(c.startswith("pgrst.") for c in (config or [])))

    def test_concurrent_runs_do_not_clash(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: db_setup.apply_migrations(self.dsn), range(4)))
        self.assertEqual(sorted(len(r) for r in results), [0, 0, 0, 1])


if __name__ == "__main__":
    unittest.main()
