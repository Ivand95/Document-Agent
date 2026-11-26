"""Audit log: who changed access, and who was refused an admin area.

Stores the action and its parameters, never the content of questions, answers or documents.
Writing is best effort: a failure is logged but never undoes or blocks the action itself.
"""
import logging

from core.config import SUPABASE_SCHEMA, global_supabase_client

log = logging.getLogger("audit")

MAX_LIMIT = 500


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def actor_of(claims: dict | None) -> tuple[str | None, str | None]:
    """(user id, readable name) of whoever made the request."""
    claims = claims or {}
    return claims.get("user_id"), claims.get("sub") or claims.get("name")


def record(claims, action: str, resource_type=None, resource_id=None, outcome: str = "ok", details=None):
    user_id, actor = actor_of(claims)
    row = {
        "user_id": user_id,
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": None if resource_id is None else str(resource_id),
        "outcome": outcome,
        "details": details or {},
    }
    try:
        _db().table("audit_log").insert(row).execute()
    except Exception:
        log.exception("Could not write the audit entry for %s (%s)", action, outcome)


def list_entries(
    user_id=None, action=None, resource_type=None, resource_id=None, since=None, until=None,
    limit: int = 100, offset: int = 0, action_prefixes: list[str] | None = None,
) -> list[dict]:
    """Newest first. action_prefixes: keep the entries whose action starts with any of them.

    The caller validates the prefixes (lowercase letters, "_" and a final "."). In LIKE, "_" matches any one
    character, so "prompt_tool." would also match "promptXtool.": no such action exists.
    """
    query = _db().table("audit_log").select("*")
    if user_id:
        query = query.eq("user_id", str(user_id))
    if action:
        query = query.eq("action", action)
    if action_prefixes:
        query = query.or_(",".join(f'action.like."{prefix}*"' for prefix in action_prefixes))
    if resource_type:
        query = query.eq("resource_type", resource_type)
    if resource_id:
        query = query.eq("resource_id", resource_id)
    if since:
        query = query.gte("at", since.isoformat())
    if until:
        query = query.lt("at", until.isoformat())
    limit = max(1, min(limit, MAX_LIMIT))
    return query.order("at", desc=True).range(offset, offset + limit - 1).execute().data
