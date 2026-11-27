import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from db import db_setup  # noqa: E402

DIM = db_setup.EMBEDDING_DIM


ALL_MIGRATIONS = [p.stem for p in db_setup.migration_files()]


def unit_vector(i):
    """Orthogonal vectors: identical -> similarity 1, different -> 0."""
    return "[" + ",".join("1" if j == i else "0" for j in range(DIM)) + "]"


class RenderTest(unittest.TestCase):
    def test_placeholders_are_all_replaced(self):
        for path in db_setup.migration_files():
            sql = db_setup.render(path.read_text(), "extensions")
            self.assertNotIn("@@", sql, path.name)
            if path.name.startswith("0001_"):
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

    ALL = ["HR", "IT", "OTROS"]

    def search(self, conn, query, threshold=0.5, count=10, categories=ALL):
        return conn.execute(
            f"select content, similarity from {self.schema}.match_documents(%s::vector, %s, %s, %s::text[]) order by similarity desc, content",
            (unit_vector(query), threshold, count, categories),
        ).fetchall()

    def test_fresh_database_gets_tables_functions_and_working_search(self):
        applied = db_setup.apply_migrations(self.dsn)
        self.assertEqual(applied, ALL_MIGRATIONS)

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
                f"select content from {self.schema}.match_conversations(%s::vector)", (unit_vector(0),)
            ).fetchall()
            self.assertEqual({r[0] for r in rows}, {"hola", "adios"})

    def test_document_search_denies_by_default(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            self.fill(c)
            self.assertEqual(self.search(c, 0, categories=None), [])  # no filter given
            self.assertEqual(self.search(c, 0, categories=[]), [])
            # the default argument is the same as NULL
            rows = c.execute(
                f"select content from {self.schema}.match_documents(%s::vector, -1)", (unit_vector(0),)
            ).fetchall()
            self.assertEqual(rows, [])

    def test_document_search_only_returns_allowed_categories(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            self.fill(c)
            seen = lambda cats: {h[0] for h in self.search(c, 0, threshold=-1, categories=cats)}  # noqa: E731
            self.assertEqual(seen(["HR"]), {"vacaciones"})
            self.assertEqual(seen(["IT", "OTROS"]), {"soporte", "general"})
            self.assertEqual(seen(["FI"]), set())
            self.assertEqual(seen(["hr"]), set())  # category codes are exact, not case-folded
            self.assertEqual(seen(["HR", "IT", "OTROS"]), {"vacaciones", "soporte", "general"})

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
        self.assertEqual(db_setup.apply_migrations(self.dsn), ALL_MIGRATIONS)
        with self.connect() as c:
            self.fill(c)
            self.assertEqual([h[0] for h in self.search(c, 0, categories=["HR"])], ["vacaciones"])
            tracked = c.execute(f"select version from public.{db_setup.TRACKING_TABLE}").fetchall()
            self.assertEqual(tracked, [("0001_vector_search",), ("0002_deny_by_default",), ("0003_ingestion_admin",)])
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
        self.assertEqual(sorted(len(r) for r in results), [0, 0, 0, len(ALL_MIGRATIONS)])  # one runner applies them all

    # --- Users (0004) ---
    def upsert(self, c, oid="oid-1", email="Ana@Coop.test", department="IT", position="Analista", default="active"):
        return c.execute(
            f"select user_id, user_status, user_perm_version, created from {self.schema}.upsert_m365_user(%s, %s, %s, %s, %s, %s)",
            (oid, email, "Ana", department, position, default),
        ).fetchone()

    def test_first_login_registers_and_later_logins_refresh_the_profile(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            user_id, status, version, created = self.upsert(c, default="pending")
            self.assertEqual((status, version, created), ("pending", 1, True))

            again = self.upsert(c, email="ana.nueva@coop.test", department="  ", position="GR - Ventas", default="active")
            self.assertEqual(again, (user_id, "pending", 1, False))  # the status is never changed by a login
            row = c.execute(f"select email, department, position, first_login_at <= last_login_at "
                            f"from {self.schema}.users where id = %s", (user_id,)).fetchone()
            self.assertEqual(row, ("ana.nueva@coop.test", None, "GR - Ventas", True))

            for bad in ({"oid": " "}, {"default": "disabled"}):
                with self.assertRaises(psycopg.errors.RaiseException):
                    self.upsert(c, **bad)

    def test_an_email_moved_to_another_account_is_released(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            old_id = self.upsert(c, oid="old", email="ventas@coop.test")[0]
            new_id = self.upsert(c, oid="new", email="VENTAS@coop.test")[0]
            emails = dict(c.execute(f"select id, email from {self.schema}.users").fetchall())
            self.assertEqual(emails, {old_id: None, new_id: "ventas@coop.test"})

    def test_status_changes_bump_perm_version_only_when_they_change_something(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            user_id = self.upsert(c)[0]
            change = lambda status: c.execute(  # noqa: E731
                f"select user_status, user_perm_version from {self.schema}.set_user_status(%s, %s, 'admin')",
                (user_id, status),
            ).fetchone()
            self.assertEqual(change("disabled"), ("disabled", 2))
            self.assertEqual(change("disabled"), ("disabled", 2))
            self.assertEqual(change("active"), ("active", 3))
            self.assertIsNone(c.execute(
                f"select * from {self.schema}.set_user_status(gen_random_uuid(), 'active', 'admin')").fetchone())
            with self.assertRaises(psycopg.errors.CheckViolation):
                change("root")

    def test_only_the_service_role_can_register_users(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            self.assertTrue(self.upsert(c)[3])
            c.execute("reset role")
            for role in ("anon", "authenticated"):
                c.execute(f"set role {role}")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=role):
                    self.upsert(c, oid=f"x-{role}")
                c.execute("reset role")

    # --- RBAC (0005) ---
    def seeded_policy(self, c):
        from rbac.model import Mapping, Policy, Role, Rule

        q = lambda sql: c.execute(sql.replace("@", self.schema + ".")).fetchall()  # noqa: E731
        roles = {str(r[0]): Role(str(r[0]), r[1], r[2], r[3], r[4]) for r in q("select id, key, name, description, is_system from @roles")}
        mappings = tuple(Mapping(str(m[0]), m[1], m[2], str(m[3])) for m in q("select id, kind, value, role_id from @role_mappings"))
        rules = {}
        for p in q("select id, subject_type, subject_id, resource_type, resource_id, action, effect, constraints "
                   "from @permissions where subject_type = 'role'"):
            rules.setdefault(str(p[2]), []).append(Rule(str(p[0]), *p[1:2], str(p[2]), *p[3:]))
        return Policy(roles, mappings, {k: tuple(v) for k, v in rules.items()})

    def test_rbac_seed_reproduces_the_old_rules(self):
        from core.config import DEPARTMENT_LIST, get_department_categories
        from rbac.model import Access, Subject

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")  # read the way the backend reads it
            policy = self.seeded_policy(c)
        for department in (*DEPARTMENT_LIST, None, "", "Desconocido"):
            for position in (None, "", "Analista", "GR - Director", "gr - x", " GR - x"):
                access = Access(Subject("u", "active", 1, department, position), policy)
                self.assertEqual(sorted(access.doc_categories()), sorted(set(get_department_categories(department, position))))
                self.assertEqual(access.can("use", "agent", "audio"), bool(position and position.startswith("GR -")))
                self.assertEqual(bool(access.allowed_ids("admin", "admin_area")), department == "Tecnología")

    def test_rbac_changes_bump_perm_version_and_follow_deleted_roles(self):
        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            user_id, other = self.upsert(c)[0], self.upsert(c, oid="oid-2", email="b@coop.test")[0]
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            version = lambda u=user_id: q("select perm_version from @@users where id = %s", u)[0][0]  # noqa: E731
            role = q("select id from @@roles where key = 'audio_user'")[0][0]
            q("insert into @@user_roles (user_id, role_id, granted_by) values (%s, %s, 'x') returning 1", user_id, role)
            self.assertEqual(version(), 2)
            q("insert into @@permissions (subject_type, subject_id, resource_type, resource_id, action) "
              "values ('user', %s, 'agent', 'audio', 'use') returning 1", user_id)
            self.assertEqual(version(), 3)
            q("update @@permissions set effect = 'deny' where subject_id = %s returning 1", user_id)
            self.assertEqual(version(), 4)  # once per change
            q("insert into @@permissions (subject_type, subject_id, resource_type, resource_id, action) "
              "values ('role', %s, 'model', 'x', 'use') returning 1", role)
            self.assertEqual((version(), version(other)), (5, 1))  # only holders of the role

            custom = q("insert into @@roles (key, name) values ('contadores', 'C') returning id")[0][0]
            q("insert into @@user_roles (user_id, role_id, granted_by) values (%s, %s, 'x') returning 1", other, custom)
            q("insert into @@permissions (subject_type, subject_id, resource_type, resource_id, action) "
              "values ('role', %s, 'agent', 'audio', 'use') returning 1", custom)
            q("delete from @@roles where id = %s returning 1", custom)
            self.assertEqual(q("select count(*) from @@permissions where subject_id = %s", custom)[0][0], 0)
            self.assertEqual(q("select count(*) from @@user_roles where role_id = %s", custom)[0][0], 0)
            q("insert into @@audit_log (user_id, action) values (%s, 'test') returning id", user_id)

    def test_only_the_service_role_reads_rbac_tables(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            for role in ("anon", "authenticated"):
                c.execute(f"set role {role}")
                for table in ("roles", "user_roles", "role_mappings", "permissions", "audit_log",
                              "capabilities", "capability_operations", "connections",
                              "llm_providers", "llm_models", "llm_usage"):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=(role, table)):
                        c.execute(f"select 1 from {self.schema}.{table}")
                c.execute("reset role")

    # --- Capability registry (0006) ---
    def test_capability_registry_rules(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            user_id = self.upsert(c)[0]
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            version = lambda: q("select perm_version from @@users where id = %s", user_id)[0][0]  # noqa: E731

            # The admin role manages the registry
            self.assertEqual(q("select count(*) from @@permissions p join @@roles r on r.id = p.subject_id "
                               "where r.key = 'admin' and p.resource_type = 'admin_area' "
                               "and p.resource_id = 'capabilities' and p.action = 'admin'")[0][0], 1)

            cap = q("insert into @@capabilities (key, kind, name) values ('ads', 'mcp', 'Ads') "
                    "returning id, enabled, data_class, risk")[0]
            self.assertEqual(cap[1:], (False, "sensitive", "read"))  # everything new starts closed
            q("insert into @@capability_operations (capability_id, operation) values (%s, 'get_insights') "
              "returning enabled, read_only", cap[0])
            self.assertEqual(q("select enabled, read_only from @@capability_operations")[0], (False, False))
            conn_id = q("insert into @@connections (capability_id, name, auth_type) values (%s, 'main', 'token') "
                        "returning id", cap[0])[0][0]
            q("insert into @@connections (capability_id, name, auth_type) values (null, 'openrouter', 'token') "
              "returning id")
            for sql in ("insert into @@connections (capability_id, name, auth_type) values (null, 'openrouter', 'token')",
                        "insert into @@connections (capability_id, name, auth_type) values ('%s', 'main', 'token')"
                        % cap[0],
                        "insert into @@capabilities (key, kind, name) values ('ads', 'cli', 'x')"):
                with self.assertRaises(psycopg.errors.UniqueViolation, msg=sql):
                    with c.transaction():
                        q(sql + " returning 1")
            for sql in ("insert into @@capabilities (key, kind, name) values ('Bad Key', 'mcp', 'x')",
                        "insert into @@capabilities (key, kind, name) values ('okkey', 'plugin', 'x')",
                        "insert into @@capabilities (key, kind, name, config) values ('okkey', 'mcp', 'x', '[]')",
                        "insert into @@capability_operations (capability_id, operation) values ('%s', 'bad op')"
                        % cap[0],
                        "update @@connections set status = 'great'",
                        "update @@capabilities set key = 'renamed'"):
                with self.assertRaises(psycopg.errors.CheckViolation, msg=sql):
                    with c.transaction():
                        q(sql + " returning 1")

            q("insert into @@permissions (subject_type, subject_id, resource_type, resource_id, action) "
              "values ('user', %s, 'capability', 'ads', 'use') returning 1", user_id)
            self.assertEqual(version(), 2)
            q("delete from @@capabilities where id = %s returning 1", cap[0])
            for table in ("capability_operations", "connections where capability_id is not null", "permissions "
                          "where resource_type = 'capability' and resource_id = 'ads'"):
                self.assertEqual(q(f"select count(*) from @@{table}")[0][0], 0, table)
            self.assertEqual(version(), 3)  # the dropped grant bumps the user's version
            self.assertEqual(q("select count(*) from @@connections where id = %s", conn_id)[0][0], 0)

    # --- LLM models (0007) ---
    def test_llm_model_allowlist_rules(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            user_id = self.upsert(c)[0]
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            providers = dict(q("select key, enabled from @@llm_providers"))
            self.assertEqual(providers, {"openai": True, "openrouter": False})
            self.assertEqual(q("select count(*) from @@permissions p join @@roles r on r.id = p.subject_id "
                               "where r.key = 'admin' and p.resource_id = 'models'")[0][0], 1)
            openrouter = q("select id from @@llm_providers where key = 'openrouter'")[0][0]
            model = q("insert into @@llm_models (provider_id, model_id) values (%s, 'vendor/x') "
                      "returning id, enabled, max_data_class, routing", openrouter)[0]
            self.assertEqual(model[1:], (False, "internal", {"data_collection": "deny"}))
            for sql in ("update @@llm_models set model_id = 'vendor/y'",
                        "update @@llm_models set max_data_class = 'secret'",
                        "update @@llm_models set price_in = -1",
                        "insert into @@llm_usage (provider, model, outcome) values ('openai', 'openai:x', 'maybe')"):
                with self.assertRaises(psycopg.errors.CheckViolation, msg=sql):
                    with c.transaction():
                        q(sql + " returning 1")
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with c.transaction():
                    q("insert into @@llm_models (provider_id, model_id) values (%s, 'vendor/x') returning 1", openrouter)

            # The key lives in a connection with no capability; deleting it unlinks the provider
            conn = q("insert into @@connections (name, auth_type) values ('llm:openrouter', 'token') returning id")[0][0]
            q("update @@llm_providers set connection_id = %s where id = %s returning 1", conn, openrouter)
            q("delete from @@connections where id = %s returning 1", conn)
            self.assertIsNone(q("select connection_id from @@llm_providers where id = %s", openrouter)[0][0])

            q("insert into @@permissions (subject_type, subject_id, resource_type, resource_id, action) "
              "values ('user', %s, 'model', 'openrouter:vendor/x', 'use') returning 1", user_id)
            q("insert into @@llm_usage (user_id, provider, model, tokens_in, cost_usd, cost_source) "
              "values (%s, 'openrouter', 'openrouter:vendor/x', 5, 0.01, 'provider') returning id", user_id)
            q("delete from @@llm_models where id = %s returning 1", model[0])
            self.assertEqual(q("select count(*) from @@permissions where resource_type = 'model' "
                               "and resource_id = 'openrouter:vendor/x'")[0][0], 0)
            self.assertEqual(q("select count(*) from @@llm_usage")[0][0], 1)  # usage history stays

    def test_prompt_tool_test_results(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            areas = q("select r.key from @@permissions p join @@roles r on r.id = p.subject_id "
                      "where p.resource_type = 'admin_area' and p.resource_id = 'prompt_tools' order by 1")
            self.assertEqual([a[0] for a in areas], ["admin", "tool_admin"])
            tool = q("insert into @@capabilities (key, kind, name, data_class) values ('classify', 'prompt_tool', "
                     "'Clasificar', 'internal') returning id, last_test")[0]
            self.assertIsNone(tool[1])
            q("update @@capabilities set last_test = '{\"version\": 1, \"ok\": true}' where id = %s returning 1",
              tool[0])
            with self.assertRaises(psycopg.errors.CheckViolation):
                with c.transaction():
                    q("update @@capabilities set last_test = '[1]' where id = %s returning 1", tool[0])

    def test_department_admins(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            user_id = self.upsert(c)[0]
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            grant = q("select p.resource_id, p.constraints from @@permissions p join @@roles r on r.id = p.subject_id "
                      "where r.key = 'department_admin'")
            self.assertEqual(grant, [("users", {"scope": "departments"})])
            q("insert into @@user_admin_departments (user_id, department) values (%s, 'Comercial') returning 1", user_id)
            for sql in ("insert into @@user_admin_departments (user_id, department) values (%s, 'Comercial')",
                        "insert into @@user_admin_departments (user_id, department) values (%s, '  ')"):
                with self.assertRaises((psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation), msg=sql):
                    with c.transaction():
                        q(sql + " returning 1", user_id)
            q("delete from @@users where id = %s returning 1", user_id)
            self.assertEqual(q("select count(*) from @@user_admin_departments")[0][0], 0)

    def test_manager_assignable_roles_are_seeded_once(self):
        db_setup.apply_migrations(self.dsn)
        migration = db_setup.MIGRATIONS_DIR / "0010_manager_assignable_roles.sql"
        with self.connect() as c:
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            marked = q("select key from @@roles where manager_assignable order by key")
            self.assertEqual([m[0] for m in marked], ["audio_user", "report_user"])
            q("update @@roles set manager_assignable = false where key = 'report_user' returning 1")
            c.execute(db_setup.render(migration.read_text(), "public"))  # a re-run keeps the admin's choice
            marked = q("select key from @@roles where manager_assignable order by key")
            self.assertEqual([m[0] for m in marked], ["audio_user"])

    def test_cli_commands_column(self):
        import psycopg

        db_setup.apply_migrations(self.dsn)
        with self.connect() as c:
            c.execute("set role service_role")
            q = lambda sql, *a: c.execute(sql.replace("@@", self.schema + "."), a).fetchall()  # noqa: E731
            cap = q("insert into @@capabilities (key, kind, name) values ('meta_cli', 'cli', 'Meta') returning id")[0][0]
            q("insert into @@capability_operations (capability_id, operation, command) "
              "values (%s, 'insights', '[\"insights\", \"{account}\"]') returning 1", cap)
            self.assertEqual(q("select command from @@capability_operations")[0][0], ["insights", "{account}"])
            with self.assertRaises(psycopg.errors.CheckViolation):
                with c.transaction():
                    q("update @@capability_operations set command = '{\"a\": 1}' returning 1")


if __name__ == "__main__":
    unittest.main()
