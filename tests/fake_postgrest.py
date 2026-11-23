"""An in-memory stand-in for the PostgREST client, enough for the capability registry.

Supports table(...).select/insert/update/upsert/delete with eq, in_, not_.is_, order, range, limit, and the
defaults, unique keys and cascades of migration 0006 that the code relies on. The SQL itself is tested
on Postgres (test_db_setup); this lets the Python above it run for real.
"""
import copy
import uuid
from datetime import datetime, timezone
from unittest import mock

from postgrest.exceptions import APIError


def _now():
    return datetime.now(timezone.utc).isoformat()


DEFAULTS = {
    "capabilities": lambda: {
        "id": str(uuid.uuid4()), "description": "", "enabled": False, "risk": "read", "data_class": "sensitive",
        "config": {}, "version": 1, "created_at": _now(), "created_by": "system", "updated_at": None,
        "updated_by": None,
    },
    "capability_operations": lambda: {
        "description": "", "enabled": False, "read_only": False, "arg_schema": {"type": "object"}, "limits": {},
        "created_at": _now(), "updated_at": None, "updated_by": None,
    },
    "connections": lambda: {
        "id": str(uuid.uuid4()), "capability_id": None, "config": {}, "scopes": [], "secret_ciphertext": None,
        "secret_updated_at": None, "status": "unchecked", "status_detail": None, "last_check_at": None,
        "owner_user_id": None, "created_at": _now(), "created_by": "system", "updated_at": None, "updated_by": None,
    },
}
UNIQUE = {
    "capabilities": [("key",)],
    "capability_operations": [("capability_id", "operation")],
    "connections": [("capability_id", "name")],
}
CASCADE = {"capabilities": [("capability_operations", "capability_id"), ("connections", "capability_id")]}


def _error(code, message):
    return APIError({"code": code, "message": message, "details": None, "hint": None})


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.action, self.payload, self.fields = "select", None, "*"

    # building
    def select(self, fields="*", **_):
        self.fields = fields
        return self

    def insert(self, rows):
        self.action, self.payload = "insert", rows
        return self

    def upsert(self, rows, **_):
        self.action, self.payload = "upsert", rows
        return self

    def update(self, changes):
        self.action, self.payload = "update", changes
        return self

    def delete(self):
        self.action = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(lambda r: str(r.get(column)) == str(value))
        return self

    def in_(self, column, values):
        values = {str(v) for v in values}
        self.filters.append(lambda r: str(r.get(column)) in values)
        return self

    @property
    def not_(self):
        query = self

        class Negated:
            def is_(self, column, value):
                assert value == "null"
                query.filters.append(lambda r: r.get(column) is not None)
                return query

        return Negated()

    def order(self, *_, **__):
        return self

    def range(self, *_):
        return self

    def limit(self, *_):
        return self

    # running
    def _project(self, row):
        if self.fields == "*":
            return copy.deepcopy(row)
        return {f: copy.deepcopy(row.get(f)) for f in self.fields.split(",")}

    def _matches(self):
        return [r for r in self.db.tables.setdefault(self.table, []) if all(f(r) for f in self.filters)]

    def _check_unique(self, row, ignore=None):
        for columns in UNIQUE.get(self.table, []):
            for other in self.db.tables.get(self.table, []):
                if other is not ignore and all(str(other.get(c)) == str(row.get(c)) for c in columns):
                    raise _error("23505", f"duplicate key on {self.table} {columns}")

    def execute(self):
        if self.db.fail:
            raise RuntimeError("database down")
        rows = self.db.tables.setdefault(self.table, [])
        if self.action == "select":
            return mock.Mock(data=[self._project(r) for r in self._matches()])
        if self.action in ("insert", "upsert"):
            created = []
            for payload in self.payload if isinstance(self.payload, list) else [self.payload]:
                row = {**DEFAULTS.get(self.table, dict)(), **copy.deepcopy(payload)}
                self._check_unique(row)
                rows.append(row)
                created.append(copy.deepcopy(row))
            return mock.Mock(data=created)
        if self.action == "update":
            changed = []
            for row in self._matches():
                if self.table == "capabilities" and "key" in self.payload and self.payload["key"] != row["key"]:
                    raise _error("23514", "capabilities.key cannot change")
                candidate = {**row, **copy.deepcopy(self.payload)}
                self._check_unique(candidate, ignore=row)
                row.update(copy.deepcopy(self.payload))
                changed.append(copy.deepcopy(row))
            return mock.Mock(data=changed)
        if self.action == "delete":
            gone = self._matches()
            self.db.tables[self.table] = [r for r in rows if r not in gone]
            for row in gone:
                for child, column in CASCADE.get(self.table, []):
                    self.db.tables[child] = [c for c in self.db.tables.get(child, []) if c.get(column) != row["id"]]
            return mock.Mock(data=[copy.deepcopy(r) for r in gone])
        raise AssertionError(self.action)


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list] = {}
        self.fail = False

    def table(self, name):
        return Query(self, name)

    def __call__(self):  # stands in for registry._db()
        return self
