"""Admin API for prompt tools (WP4b): build, test, enable.

Everything needs `admin` on the `prompt_tools` admin area (seeded for the admin and tool_admin roles).
- A tool is created disabled. It can be enabled only after a successful test run of its current version.
- Changing its content (template, schemas, model, limits, data class) needs it disabled, and makes a new
  version that must be tested again.
- The data class is what the tool may receive and returns, and must fit its model (an OpenRouter model
  is `internal` at most unless a super_admin raised it). Unlike other capabilities, setting it below
  `sensitive` needs no super_admin: for a prompt tool it is a limit on its input, not a claim about an
  outside source.
- The test run calls the model as the admin (they need `use` on it, and it counts against their budget).
Who may *use* a tool is set in the permission matrix (resource_type "capability", id = its key); users
also need `use` on its model.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from api.deps import get_access, require
from capabilities import prompt_tools, registry
from models.prompt_tools import PromptToolCreate, PromptToolTest, PromptToolUpdate
from rbac import audit
from rbac.model import Access

router = APIRouter(prefix="/admin/prompt-tools")

require_prompt_tools_admin = require("admin", "admin_area", "prompt_tools")


def by(claims: dict) -> str:
    return claims.get("sub") or claims.get("user_id") or "?"


def tool_or_404(capability_id) -> dict:
    tool = prompt_tools.get(capability_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Herramienta no encontrada.")
    return tool


def summary(changes: dict) -> dict:
    """What the audit log keeps of a change: the fields, and the small values (not templates or schemas)."""
    shown = {k: v for k, v in changes.items() if k in ("name", "enabled", "data_class")}
    config = changes.get("config") or {}
    shown.update({f"config.{k}": v for k, v in config.items() if k in ("model", "max_input_chars", "max_tokens",
                                                                        "timeout_seconds")})
    shown["fields"] = sorted(list(changes) + [f"config.{k}" for k in config])
    return shown


@router.get("")
def list_endpoint(_claims: dict = Depends(require_prompt_tools_admin)):
    return {"tools": prompt_tools.list_tools()}


@router.get("/{capability_id}")
def get_endpoint(capability_id: uuid.UUID, _claims: dict = Depends(require_prompt_tools_admin)):
    return tool_or_404(capability_id)


@router.post("", status_code=201)
def create_endpoint(request: PromptToolCreate, claims: dict = Depends(require_prompt_tools_admin)):
    try:
        tool = prompt_tools.create(request.key, request.name, request.description, request.data_class,
                                   request.config.model_dump(exclude_none=True), request.input_schema, by(claims))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise HTTPException(status_code=409, detail="Ya existe una capacidad con esa clave.")
        raise
    audit.record(claims, "prompt_tool.create", "capability", tool["key"],
                 details={"data_class": tool["data_class"], "model": tool["config"]["model"]})
    return tool


@router.patch("/{capability_id}")
def update_endpoint(capability_id: uuid.UUID, request: PromptToolUpdate,
                    claims: dict = Depends(require_prompt_tools_admin)):
    tool = tool_or_404(capability_id)
    changes = request.model_dump(exclude_none=True)
    if request.config is not None:
        changes["config"] = request.config.model_dump(exclude_unset=True)  # keeps output_schema: null
    if not changes:
        return tool
    try:
        updated = prompt_tools.update(tool, changes, by(claims))
    except prompt_tools.Locked as e:
        raise HTTPException(status_code=409, detail=str(e))
    except prompt_tools.NotReady as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(claims, "prompt_tool.update", "capability", tool["key"],
                 details={**summary(changes), "version": updated["version"]})
    return updated


@router.post("/{capability_id}/test")
async def test_endpoint(capability_id: uuid.UUID, request: PromptToolTest,
                        claims: dict = Depends(require_prompt_tools_admin), actor: Access = Depends(get_access)):
    """Runs the tool on sample arguments (enabled or not). 200 with ok=false when the tool fails."""
    tool = tool_or_404(capability_id)
    result = await prompt_tools.test_run(actor, tool, request.args, request.data_class, by(claims))
    audit.record(claims, "prompt_tool.test", "capability", tool["key"], outcome="ok" if result["ok"] else "error",
                 details={"version": result["version"], "error": (result["error"] or {}).get("code"),
                          "model": result["model"], "duration_ms": result["duration_ms"]})
    return result


@router.delete("/{capability_id}", status_code=204)
def delete_endpoint(capability_id: uuid.UUID, claims: dict = Depends(require_prompt_tools_admin)):
    """Also deletes every permission on it."""
    tool = tool_or_404(capability_id)
    registry.delete_capability(tool["id"])
    audit.record(claims, "prompt_tool.delete", "capability", tool["key"])
