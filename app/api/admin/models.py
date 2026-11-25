"""Admin API for LLM providers, the model allowlist and usage (WP4b).

Everything needs `admin` on the `models` admin area. On top of that, because these decide whether
member data can leave the trusted path:
- only a super_admin raises a non-OpenAI model's max_data_class to `sensitive`;
- only a super_admin sets an OpenRouter model's routing to allow providers that collect data
  (data_collection "allow");
- the OpenRouter key is write-only: stored encrypted, never returned, never written to the audit log.
Who may *use* a model is set in the permission matrix (/admin/rbac, resource_type "model", id
"<provider>:<model_id>"), and so is the monthly budget (constraints.llm_budget_usd_month).
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError

from api.deps import get_access, require
from capabilities import secrets
from core import llm
from models.llm_models import ModelCreate, ModelUpdate, ProviderKey, ProviderUpdate
from providers import registry, usage
from rbac import audit
from rbac.model import Access

log = logging.getLogger("providers")

router = APIRouter(prefix="/admin/models")

require_models_admin = require("admin", "admin_area", "models")

ONLY_SUPER_ADMIN = "Solo un super administrador puede hacer este cambio."


def by(claims: dict) -> str:
    return claims.get("sub") or claims.get("user_id") or "?"


def provider_or_404(key: str) -> dict:
    provider = registry.get_provider(key)
    if provider is None:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    return provider


def model_or_404(model_id) -> dict:
    model = registry.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado.")
    return model


# --- Providers -------------------------------------------------------------------------------------------


@router.get("/providers")
def providers_endpoint(_claims: dict = Depends(require_models_admin)):
    """runtime_enabled: OPENROUTER_ENABLED; both it and `enabled` must be on for OpenRouter calls."""
    return {"providers": registry.list_providers(), "secrets_configured": secrets.configured()}


@router.patch("/providers/{key}")
def update_provider_endpoint(key: str, request: ProviderUpdate, claims: dict = Depends(require_models_admin)):
    provider = provider_or_404(key)
    row = registry.set_provider_enabled(provider, request.enabled, by(claims))
    audit.record(claims, "provider.update", "provider", key, details={"enabled": request.enabled})
    return row


@router.put("/providers/{key}/key")
def set_key_endpoint(key: str, request: ProviderKey, claims: dict = Depends(require_models_admin)):
    provider = provider_or_404(key)
    try:
        connection = registry.set_provider_key(provider, request.api_key, claims.get("user_id"), by(claims))
    except secrets.SecretsUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.record(claims, "provider.key_set", "provider", key)  # never the key
    return connection


@router.delete("/providers/{key}/key", status_code=204)
def clear_key_endpoint(key: str, claims: dict = Depends(require_models_admin)):
    registry.clear_provider_key(provider_or_404(key), by(claims))
    audit.record(claims, "provider.key_clear", "provider", key)


@router.post("/providers/{key}/check")
def check_provider_endpoint(key: str, claims: dict = Depends(require_models_admin)):
    try:
        result = registry.check_provider(provider_or_404(key))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.record(claims, "provider.check", "provider", key, outcome="ok" if result["status"] == "ok" else "error",
                 details={"status": result["status"]})
    return result


@router.post("/providers/{key}/discover")
def discover_endpoint(key: str, claims: dict = Depends(require_models_admin)):
    """Copies the provider's model list into the allowlist (new models disabled)."""
    provider = provider_or_404(key)
    try:
        report = registry.discover(provider, by(claims))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.warning("Model discovery for %s failed: %s", key, type(e).__name__)
        raise HTTPException(status_code=502, detail="No se pudo leer la lista de modelos del proveedor.")
    audit.record(claims, "provider.discover", "provider", key, details=report)
    return report


# --- Usage (declared before /{model_id}) ------------------------------------------------------------------


@router.get("/usage")
def usage_endpoint(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    group_by: str = Query("model"),
    _claims: dict = Depends(require_models_admin),
):
    """Calls, tokens and cost of a month (default: this one), grouped by user_id, model, agent_key,
    capability_key or provider."""
    when = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc) if month else datetime.now(timezone.utc)
    try:
        rows = usage.summary(when, group_by)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"month": usage.month_start(when).strftime("%Y-%m"), "group_by": group_by, "rows": rows}


# --- Models ------------------------------------------------------------------------------------------------


@router.get("")
def list_endpoint(
    provider: str | None = Query(None), enabled: bool | None = Query(None), q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    _claims: dict = Depends(require_models_admin),
):
    return registry.list_models(provider, enabled, q, limit, offset)


@router.get("/{model_id}")
def get_endpoint(model_id: uuid.UUID, _claims: dict = Depends(require_models_admin)):
    return model_or_404(model_id)


@router.post("", status_code=201)
def create_endpoint(request: ModelCreate, claims: dict = Depends(require_models_admin)):
    provider = provider_or_404(request.provider)
    try:
        model = registry.add_model(provider, request.model_id, request.name, request.price_in, request.price_out,
                                   request.supports_tools, request.supports_json_schema, request.context_length,
                                   by(claims))
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise HTTPException(status_code=409, detail="Ya existe.")
        raise
    audit.record(claims, "model.create", "model", model["ref"])
    return model


@router.patch("/{model_id}")
def update_endpoint(
    model_id: uuid.UUID, request: ModelUpdate,
    claims: dict = Depends(require_models_admin), actor: Access = Depends(get_access),
):
    model = model_or_404(model_id)
    changes = request.model_dump(exclude_none=True)
    if not changes:
        return model
    discovered = model["provider"] != llm.OPENAI
    if discovered and set(changes) & {"price_in", "price_out", "supports_tools", "supports_json_schema"}:
        raise HTTPException(status_code=400, detail="Los precios y capacidades de este modelo vienen del proveedor.")
    if changes.get("max_data_class") == "sensitive" and discovered and model["max_data_class"] != "sensitive" \
            and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    if "routing" in changes:
        if not discovered:
            raise HTTPException(status_code=400, detail="Las preferencias de enrutamiento son de OpenRouter.")
        try:
            changes["routing"] = registry.validate_routing(changes["routing"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if changes["routing"]["data_collection"] == "allow" and \
                (model.get("routing") or {}).get("data_collection") != "allow" and not actor.is_super_admin:
            raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    if changes.get("enabled") and not model["available"]:
        raise HTTPException(status_code=409, detail="El proveedor ya no ofrece este modelo.")
    updated = registry.update_model(model, changes, by(claims))
    audit.record(claims, "model.update", "model", model["ref"],
                 details={"changes": changes, "before": {k: model.get(k) for k in changes}})
    return updated


@router.delete("/{model_id}", status_code=204)
def delete_endpoint(model_id: uuid.UUID, claims: dict = Depends(require_models_admin)):
    """Also deletes the permissions on it. A discovered model comes back (disabled) on the next discovery."""
    model = model_or_404(model_id)
    registry.delete_model(model)
    audit.record(claims, "model.delete", "model", model["ref"])
