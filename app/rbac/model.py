"""RBAC evaluation, with no database access (rbac.service loads the data).

Rules:
- Deny by default: nothing is allowed unless a permission allows it. Inactive users get nothing.
- A user's roles: the ones given to them by hand (until they expire), the ones their current department
  or position maps to, the `everyone` mappings, and, for one release, the old environment variables
  (rbac.legacy).
- Permissions given to the user directly override their roles' permissions for the same resource and
  action. Within each level an explicit deny beats any allow.
- resource_id "*" matches every resource of its type.
- Actions are independent: `admin` on something does not imply `use` of it.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.config import _plain
from rbac.catalog import ADMIN_AREAS, AGENTS, SUPER_ADMIN, WILDCARD, known_ids


@dataclass(frozen=True)
class Role:
    id: str
    key: str
    name: str
    description: str | None = None
    is_system: bool = False


@dataclass(frozen=True)
class Mapping:
    id: str
    kind: str  # department | position_prefix | everyone
    value: str
    role_id: str

    def matches(self, department: str | None, position: str | None) -> bool:
        if self.kind == "everyone":
            return True
        if self.kind == "department":
            return bool(department and department.strip()) and _plain(department) == _plain(self.value)
        if self.kind == "position_prefix":
            # Exact on purpose, like the old "GR -" rule: "gr - x" or " GR - x" are not managers
            return bool(self.value) and bool(position) and position.startswith(self.value)
        return False

    def describe(self) -> str:
        return "everyone" if self.kind == "everyone" else f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class Rule:
    id: str
    subject_type: str  # role | user
    subject_id: str
    resource_type: str
    resource_id: str
    action: str
    effect: str = "allow"
    constraints: dict = field(default_factory=dict, compare=False)

    def matches(self, action: str, resource_type: str, resource_id: str) -> bool:
        return (
            self.action == action
            and self.resource_type == resource_type
            and self.resource_id in (resource_id, WILDCARD)
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "effect": self.effect,
            "constraints": dict(self.constraints or {}),
        }


@dataclass(frozen=True)
class Grant:
    """A role given to a user by hand."""

    role_id: str
    expires_at: datetime | None = None
    granted_by: str | None = None

    def current(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True)
class Policy:
    """Everything that is the same for every user: roles, mappings and the roles' permissions."""

    roles: dict  # id -> Role
    mappings: tuple = ()
    role_rules: dict = field(default_factory=dict)  # role id -> tuple[Rule]

    def role_by_key(self, key: str) -> Role | None:
        return next((r for r in self.roles.values() if r.key == key), None)


@dataclass(frozen=True)
class Subject:
    """One user as RBAC sees them. Department and position come from their last Microsoft 365 login."""

    user_id: str
    status: str
    perm_version: int = 1
    department: str | None = None
    position: str | None = None
    grants: tuple = ()  # tuple[Grant]
    rules: tuple = ()  # tuple[Rule] given to the user directly


@dataclass(frozen=True)
class HeldRole:
    role: Role
    sources: tuple  # "assigned", "department:Marketing", "position_prefix:GR -", "everyone", "legacy:..."


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str  # inactive | user_deny | user_allow | role_deny | role_allow | no_rule
    rules: tuple = ()
    constraints: dict = field(default_factory=dict)


def merge_constraints(rules) -> dict:
    """The allows that apply are combined, most permissive wins: a limit only stays if every rule sets
    it (numbers: the highest; lists: all values together). A rule without the limit means no limit."""
    rules = list(rules)
    if not rules:
        return {}
    keys = set.intersection(*(set((r.constraints or {}).keys()) for r in rules))
    merged = {}
    for key in sorted(keys):
        values = [r.constraints[key] for r in rules]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            merged[key] = max(values)
        elif all(isinstance(v, list) for v in values):
            merged[key] = sorted({x for v in values for x in v}, key=str)
        elif all(v == values[0] for v in values):
            merged[key] = values[0]
    return merged


def resolve_roles(subject: Subject, policy: Policy, legacy_keys=(), now: datetime | None = None) -> list:
    """The roles a user holds right now, each with why they hold it."""
    now = now or datetime.now(timezone.utc)
    sources: dict = {}

    def add(role_id, source):
        if role_id in policy.roles:
            sources.setdefault(role_id, []).append(source)

    for grant in subject.grants:
        if grant.current(now):
            add(grant.role_id, "assigned")
    for mapping in policy.mappings:
        if mapping.matches(subject.department, subject.position):
            add(mapping.role_id, mapping.describe())
    for key, source in legacy_keys:
        role = policy.role_by_key(key)
        if role:
            add(role.id, source)
    return [HeldRole(policy.roles[rid], tuple(src)) for rid, src in sources.items()]


class Access:
    """What one user may do. Built by rbac.service.access_for(); cheap to query."""

    def __init__(self, subject: Subject, policy: Policy, legacy_keys=(), now: datetime | None = None):
        self.subject = subject
        self.policy = policy
        self.active = subject.status == "active"
        self.roles = resolve_roles(subject, policy, legacy_keys, now) if self.active else []
        self._role_ids = [h.role.id for h in self.roles]

    @property
    def user_id(self) -> str:
        return self.subject.user_id

    @property
    def role_keys(self) -> list[str]:
        return sorted(h.role.key for h in self.roles)

    def has_role(self, key: str) -> bool:
        return any(h.role.key == key for h in self.roles)

    @property
    def is_super_admin(self) -> bool:
        return self.has_role(SUPER_ADMIN)

    def check(self, action: str, resource_type: str, resource_id: str) -> Decision:
        if not self.active:
            return Decision(False, "inactive")

        user_rules = [r for r in self.subject.rules if r.matches(action, resource_type, resource_id)]
        if user_rules:
            denies = tuple(r for r in user_rules if r.effect == "deny")
            if denies:
                return Decision(False, "user_deny", denies)
            return Decision(True, "user_allow", tuple(user_rules), merge_constraints(user_rules))

        role_rules = [
            r
            for role_id in self._role_ids
            for r in self.policy.role_rules.get(role_id, ())
            if r.matches(action, resource_type, resource_id)
        ]
        denies = tuple(r for r in role_rules if r.effect == "deny")
        if denies:
            return Decision(False, "role_deny", denies)
        allows = tuple(r for r in role_rules if r.effect == "allow")
        if allows:
            return Decision(True, "role_allow", allows, merge_constraints(allows))
        return Decision(False, "no_rule")

    def can(self, action: str, resource_type: str, resource_id: str) -> bool:
        return self.check(action, resource_type, resource_id).allowed

    def allowed_ids(self, action: str, resource_type: str, candidates=None) -> list[str]:
        """Which of the candidates (default: every known resource of the type) are allowed."""
        ids = known_ids(resource_type) if candidates is None else list(candidates)
        return [i for i in ids if self.can(action, resource_type, i)]

    def doc_categories(self) -> list[str]:
        """Document categories this user may search. Empty means none (the search returns nothing)."""
        return self.allowed_ids("use", "doc_category")

    def summary(self) -> dict:
        """What the UI needs to decide which screens to show. The API enforces it again anyway."""
        return {
            "user_id": self.user_id,
            "perm_version": self.subject.perm_version,
            "roles": self.role_keys,
            "agents": self.allowed_ids("use", "agent", AGENTS),
            "admin_areas": self.allowed_ids("admin", "admin_area", ADMIN_AREAS),
            "doc_categories": self.doc_categories(),
        }

    def explain(self) -> dict:
        """Why this user can or cannot do each known thing: their roles (and where each comes from),
        the rules that apply, and the decision for every known resource."""
        decisions = []
        for resource_type, action in (("agent", "use"), ("admin_area", "admin"), ("doc_category", "use")):
            for resource_id in known_ids(resource_type):
                d = self.check(action, resource_type, resource_id)
                decisions.append(
                    {
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "action": action,
                        "allowed": d.allowed,
                        "reason": d.reason,
                        "rule_ids": [r.id for r in d.rules],
                        "constraints": d.constraints,
                    }
                )
        return {
            "user_id": self.user_id,
            "status": self.subject.status,
            "perm_version": self.subject.perm_version,
            "department": self.subject.department,
            "position": self.subject.position,
            "roles": [
                {"id": h.role.id, "key": h.role.key, "name": h.role.name, "sources": list(h.sources)}
                for h in self.roles
            ],
            "user_rules": [r.as_dict() for r in self.subject.rules],
            "role_rules": [r.as_dict() for rid in self._role_ids for r in self.policy.role_rules.get(rid, ())],
            "decisions": decisions,
        }
