"""An in-memory stand-in for the PostgREST client, enough for the capability registry.

Supports table(...).select/insert/update/upsert/delete with eq, in_, not_.is_, order, range, limit, and the
defaults, unique keys and cascades of migration 0006 that the code relies on. The SQL itself is tested
on Postgres (test_db_setup); this lets the Python above it run for real.
"""
import copy
import re
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
        "updated_by": None, "last_test": None,
    },
    "capability_operations": lambda: {
        "description": "", "enabled": False, "read_only": False, "arg_schema": {"type": "object"}, "limits": {},
        "command": None, "created_at": _now(), "updated_at": None, "updated_by": None,
    },
    "connections": lambda: {
        "id": str(uuid.uuid4()), "capability_id": None, "config": {}, "scopes": [], "secret_ciphertext": None,
        "secret_updated_at": None, "status": "unchecked", "status_detail": None, "last_check_at": None,
        "owner_user_id": None, "created_at": _now(), "created_by": "system", "updated_at": None, "updated_by": None,
    },
}
DEFAULTS["user_admin_departments"] = lambda: {"created_at": _now(), "created_by": "system"}
DEFAULTS.update({
    "llm_providers": lambda: {"id": str(uuid.uuid4()), "connection_id": None, "enabled": False, "updated_at": None,
                              "updated_by": None},
    "llm_models": lambda: {
        "id": str(uuid.uuid4()), "name": "", "enabled": False, "available": True, "supports_tools": False,
        "supports_json_schema": False, "context_length": None, "max_data_class": "internal",
        "routing": {"data_collection": "deny"}, "price_in": None, "price_out": None, "discovered_at": None,
        "created_at": _now(), "created_by": "system", "updated_at": None, "updated_by": None,
    },
    "llm_usage": lambda: {"at": _now(), "user_id": None, "agent_key": None, "capability_key": None, "tokens_in": 0,
                          "tokens_out": 0, "cost_usd": None, "cost_source": None, "outcome": "ok"},
})
UNIQUE = {
    "capabilities": [("key",)],
    "capability_operations": [("capability_id", "operation")],
    "connections": [("capability_id", "name")],
    "llm_providers": [("key",)],
    "llm_models": [("provider_id", "model_id")],
    "user_admin_departments": [("user_id", "department")],
}
NOT_NULL = {"llm_models": ("enabled", "available", "max_data_class", "routing", "supports_tools")}
CASCADE = {"capabilities": [("capability_operations", "capability_id"), ("connections", "capability_id")]}


def _error(code, message):
    return APIError({"code": code, "message": message, "details": None, "hint": None})


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.action, self.payload, self.fields = "select", None, "*"
        self.count, self.window = None, None

    # building
    def select(self, fields="*", count=None, **_):
        self.fields, self.count = fields, count
        return self

    def insert(self, rows):
        self.action, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict=None, default_to_null=True, **_):
        self.action, self.payload = "upsert", rows
        self.conflict = tuple(on_conflict.split(",")) if on_conflict else None
        # Like postgrest-py: a bulk upsert sets the columns it doesn't send to NULL on new rows unless
        # default_to_null=False
        self.null_missing = default_to_null and isinstance(rows, list)
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

    def gte(self, column, value):
        self.filters.append(lambda r: r.get(column) is not None and str(r.get(column)) >= str(value))
        return self

    def lt(self, column, value):
        self.filters.append(lambda r: r.get(column) is not None and str(r.get(column)) < str(value))
        return self

    def in_(self, column, values):
        values = {str(v) for v in values}
        self.filters.append(lambda r: str(r.get(column)) in values)
        return self

    def ilike(self, column, pattern):
        needle = pattern.strip("*").lower()
        self.filters.append(lambda r: needle in str(r.get(column) or "").lower())
        return self

    def or_(self, expression):
        """Only what the app sends: col.ilike."*text*",col.ilike."*text*"."""
        terms = re.findall(r'(\w+)\.ilike\."([^"]*)"', expression)
        assert terms, expression
        tests = [(column, pattern.strip("*").lower()) for column, pattern in terms]
        self.filters.append(lambda r: any(n in str(r.get(c) or "").lower() for c, n in tests))
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

    def range(self, start, end):
        self.window = (start, end + 1)
        return self

    def limit(self, n):
        self.window = (0, n)
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
            matches = self._matches()
            page = matches[slice(*self.window)] if self.window else matches
            return mock.Mock(data=[self._project(r) for r in page], count=len(matches) if self.count else None)
        if self.action in ("insert", "upsert"):
            created = []
            for payload in self.payload if isinstance(self.payload, list) else [self.payload]:
                conflict = getattr(self, "conflict", None) if self.action == "upsert" else None
                match = conflict and next((r for r in rows if all(str(r.get(c)) == str(payload.get(c))
                                                                  for c in conflict)), None)
                if match is not None:  # merge-duplicates: only the columns sent change
                    match.update(copy.deepcopy(payload))
                    created.append(copy.deepcopy(match))
                    continue
                row = {**DEFAULTS.get(self.table, dict)(), **copy.deepcopy(payload)}
                if getattr(self, "null_missing", False):
                    row = {k: (v if k in payload or k == "id" else None) for k, v in row.items()}
                    if any(row.get(c) is None for c in NOT_NULL.get(self.table, ())):
                        raise _error("23502", f"null value in a not-null column of {self.table}")
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
