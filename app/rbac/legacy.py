"""The environment variables that decided access before RBAC, kept as a fallback for one release.

ADMIN_DEPARTMENTS and AUDIO_ALLOWED_DEPARTMENTS still work when they are set: a user of a listed
department is treated as holding the `admin` or `audio_user` role (shown as "legacy:..." in the
effective-permissions view, and still subject to denies). The seeded role mappings already reproduce
the defaults (Tecnología -> admin; calls for managers only), so with the variables unset nothing
changes. Set RBAC_LEGACY_ENV=false to turn the fallback off, then remove the variables.

The other hard-coded rules (department -> document category, "GR -" managers) are seeded as data in
migration 0005; core.config.get_department_categories stays as the reference they were taken from.
"""
import os

from core.config import _plain

FALLBACKS = (
    ("ADMIN_DEPARTMENTS", "admin"),
    ("AUDIO_ALLOWED_DEPARTMENTS", "audio_user"),
)


def enabled() -> bool:
    return os.getenv("RBAC_LEGACY_ENV", "true").strip().lower() != "false"


def legacy_role_keys(department: str | None) -> list[tuple[str, str]]:
    """[(role key, source)] the old variables give this department. Only variables that are set count."""
    if not enabled() or not (department and department.strip()):
        return []
    found = []
    for variable, role_key in FALLBACKS:
        listed = {_plain(d) for d in os.getenv(variable, "").split(",") if d.strip()}
        if _plain(department) in listed:
            found.append((role_key, f"legacy:{variable}"))
    return found
