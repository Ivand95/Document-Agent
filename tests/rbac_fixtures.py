"""In-memory RBAC for tests.

The policy is read from the seeds in the migrations themselves (not a copy), so the tests check what the
database will actually contain. FakeRBAC replaces the two loaders of rbac.service, so everything above
them (cache, Access, the API dependencies) runs for real, and gives the capability registry an in-memory
database (FakeDB).

Importing this module points the capability registry at an empty FakeDB, so no test reaches a real
Supabase by accident (permission checks on capabilities read the registry).
"""
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from capabilities import registry  # noqa: E402
from core import security  # noqa: E402
from rbac import service  # noqa: E402
from rbac.model import Grant, Mapping, Policy, Role, Rule, Subject  # noqa: E402

from fake_postgrest import FakeDB  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "app" / "db" / "migrations"
MIGRATION = MIGRATIONS / "0005_rbac.sql"
PERMISSIONS_SEED = ") as p (role_key, resource_type, resource_id, action)"

registry._db = FakeDB()
NAMESPACE = uuid.UUID("6f1c1d1e-0000-4000-8000-000000000005")


def _tuples(block: str) -> list[tuple]:
    """The ('a', 'b', true) tuples of a SQL VALUES list: string literals and booleans only."""
    block = "\n".join(line for line in block.splitlines() if not line.strip().startswith("--"))
    tuples, current, depth, i = [], [], 0, 0
    while i < len(block):
        c = block[i]
        if c == "'":
            j, buf = i + 1, []
            while True:
                if block[j] == "'" and block[j + 1 : j + 2] == "'":
                    buf.append("'")
                    j += 2
                elif block[j] == "'":
                    break
                else:
                    buf.append(block[j])
                    j += 1
            current.append("".join(buf))
            i = j + 1
            continue
        if c == "(":
            depth += 1
            if depth == 1:
                current = []
        elif c == ")":
            depth -= 1
            if depth == 0:
                tuples.append(tuple(current))
        elif depth == 1 and block.startswith("true", i):
            current.append(True)
            i += 4
            continue
        elif depth == 1 and block.startswith("false", i):
            current.append(False)
            i += 5
            continue
        i += 1
    return tuples


def seed_data() -> dict:
    sql = MIGRATION.read_text()
    roles = re.search(r"insert into @@schema@@\.roles \(key, name, description, is_system\) values(.*?)on conflict", sql, re.S)
    blocks = sql.split("from (values")[1:]
    mappings = next(b.split(") as m (kind, value, role_key)")[0] for b in blocks if ") as m (kind, value, role_key)" in b)
    # Role permissions seeded by 0005 and by later migrations (e.g. 0006: admin -> capabilities)
    permissions = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for block in path.read_text().split("from (values")[1:]:
            if PERMISSIONS_SEED in block:
                permissions += _tuples(block.split(PERMISSIONS_SEED)[0])
    return {"roles": _tuples(roles.group(1)), "mappings": _tuples(mappings), "permissions": permissions}


def role_id(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, key))


def seed_policy() -> Policy:
    data = seed_data()
    roles = {
        role_id(key): Role(id=role_id(key), key=key, name=name, description=desc, is_system=system)
        for key, name, desc, system in data["roles"]
    }
    mappings = tuple(
        Mapping(id=f"m{i}", kind=kind, value=value, role_id=role_id(key))
        for i, (kind, value, key) in enumerate(data["mappings"])
    )
    role_rules: dict = {}
    for i, (key, rtype, rid, action) in enumerate(data["permissions"]):
        role_rules.setdefault(role_id(key), []).append(
            Rule(id=f"p{i}", subject_type="role", subject_id=role_id(key), resource_type=rtype,
                 resource_id=rid, action=action, effect="allow")
        )
    return Policy(roles=roles, mappings=mappings, role_rules={k: tuple(v) for k, v in role_rules.items()})


def rule(resource_type, resource_id, action="use", effect="allow", subject_type="user", subject_id="x", **constraints):
    return Rule(id=uuid.uuid4().hex, subject_type=subject_type, subject_id=subject_id, resource_type=resource_type,
                resource_id=resource_id, action=action, effect=effect, constraints=constraints)


class FakeRBAC:
    """Users and policy kept in memory, served through rbac.service's real cache and evaluation."""

    def __init__(self, policy: Policy | None = None):
        self.policy = policy or seed_policy()
        self.subjects: dict = {}
        self.fail = False  # True: the loaders raise, as if the database were down
        self.db = FakeDB()  # the capability registry's tables
        self.loads = 0
        self.audit: list = []  # (action, resource_type, resource_id, outcome, details)

    def add_user(self, department="Tecnología", position="Analista", status="active", roles=(), rules=(),
                 user_id=None) -> str:
        user_id = user_id or str(uuid.uuid4())
        grants = tuple(Grant(role_id=role_id(k)) if isinstance(k, str) else k for k in roles)
        self.subjects[user_id] = Subject(user_id=user_id, status=status, perm_version=1, department=department,
                                         position=position, grants=grants, rules=tuple(rules))
        service.invalidate(user_id)
        return user_id

    def claims(self, department="Tecnología", position="Analista", sub=None, **kwargs) -> dict:
        """Registers a user and returns the claims a login would put in their token."""
        user_id = self.add_user(department, position, **kwargs)
        return {"sub": sub or f"{user_id[:8]}@example.com", "name": "Test", "department": department,
                "position": position, "user_id": user_id, "perm_version": 1}

    def token(self, department="Tecnología", position="Analista", **kwargs) -> str:
        return security.create_access_token(self.claims(department, position, **kwargs))

    def headers(self, department="Tecnología", position="Analista", **kwargs) -> dict:
        return {"Authorization": f"Bearer {self.token(department, position, **kwargs)}"}

    # Loaders that replace the database ones
    def load_policy(self):
        if self.fail:
            raise RuntimeError("database down")
        return self.policy

    def load_subject(self, user_id):
        self.loads += 1
        if self.fail:
            raise RuntimeError("database down")
        return self.subjects.get(user_id)

    def record(self, claims, action, resource_type=None, resource_id=None, outcome="ok", details=None):
        self.audit.append((action, resource_type, None if resource_id is None else str(resource_id), outcome,
                           details or {}))

    def install(self, testcase):
        from unittest import mock

        from rbac import audit

        patchers = [mock.patch.object(service, n, getattr(self, n)) for n in ("load_policy", "load_subject")]
        patchers.append(mock.patch.object(audit, "record", self.record))
        patchers.append(mock.patch.object(registry, "_db", self.db))
        for patcher in patchers:
            patcher.start()
            testcase.addCleanup(patcher.stop)
        service.invalidate()
        registry.invalidate()
        testcase.addCleanup(service.invalidate)
        testcase.addCleanup(registry.invalidate)
        return self
