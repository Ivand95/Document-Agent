"""Which users an admin of the Usuarios area may see and change.

- Admins and super admins see everyone. A department admin (role department_admin, whose permission on
  the area carries {"scope": "departments"}) sees only the users of their own Microsoft 365 department and
  of the extra departments assigned to them (user_admin_departments). Departments are compared without
  accents or case, like the role mappings.
- Some users are protected (shown, but no actions): the admin's own account; super admins, for anyone
  who isn't one; and, for a department admin, anyone who can administer users (admins and other
  department admins).
"""
from dataclasses import dataclass

from core.config import _plain
from rbac import service as rbac
from rbac.model import Access
from users import service as users

USERS_AREA = ("admin", "admin_area", "users")
DEPARTMENT_ADMIN = "department_admin"
# Row actions -> the status they set
ACTIONS = {"approve": "active", "hold": "pending", "disable": "disabled"}


@dataclass(frozen=True)
class Scope:
    limited: bool
    departments: frozenset = frozenset()  # plain names, when limited

    def allows(self, department: str | None) -> bool:
        return not self.limited or (bool((department or "").strip()) and _plain(department) in self.departments)


def scope_of(actor: Access) -> Scope:
    """The actor's reach in the Usuarios area (callers have already required the area)."""
    decision = actor.check(*USERS_AREA)
    if decision.constraints.get("scope") != "departments":
        return Scope(limited=False)
    departments = [actor.subject.department] + [d["department"] for d in users.list_admin_departments(actor.user_id)]
    return Scope(limited=True, departments=frozenset(_plain(d) for d in departments if (d or "").strip()))


def department_options(scope: Scope) -> list[str]:
    """Departments the actor can filter by: one spelling each."""
    return [d for d in users.known_departments() if scope.allows(d)]


def department_filter(scope: Scope, wanted: str | None) -> list[str] | None:
    """The exact spellings to keep for a list (None: no filter). Outside the scope gives []."""
    if not scope.limited and not (wanted or "").strip():
        return None
    return [
        d for d in users.department_spellings()
        if scope.allows(d) and (not (wanted or "").strip() or _plain(d) == _plain(wanted))
    ]


def protection(actor: Access, scope: Scope, row: dict, target: Access | None) -> str | None:
    """Why the actor can't act on this user ("self", "super_admin", "admin"), or None."""
    if str(row["id"]) == str(actor.user_id):
        return "self"
    target_super = bool(target and target.is_super_admin)
    if target_super and not actor.is_super_admin:
        return "super_admin"
    if scope.limited and target is not None and target.can(*USERS_AREA):
        return "admin"
    return None


def annotate(actor: Access, scope: Scope, rows: list[dict]) -> list[dict]:
    """Adds `actionable`, `actions` (approve, hold, disable: those that change the status) and
    `protected` (the reason, or null) to each user row."""
    targets = rbac.accesses_if_active(rows)
    result = []
    for row in rows:
        reason = protection(actor, scope, row, targets.get(str(row["id"])))
        actions = [] if reason else [a for a, status in ACTIONS.items() if status != row.get("status")]
        result.append({**row, "actionable": reason is None, "actions": actions, "protected": reason})
    return result


def is_department_admin(row: dict) -> bool:
    target = rbac.accesses_if_active([row]).get(str(row["id"]))
    return bool(target and target.has_role(DEPARTMENT_ADMIN))
