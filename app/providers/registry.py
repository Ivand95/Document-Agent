"""Providers and the model allowlist in Supabase, cached like the capability registry.

Models are named "<provider>:<model_id>" everywhere: in permissions (resource_type "model"), in usage and
in capability configs. The OpenRouter key is a connection secret (encrypted, never returned); this module
gives it to core.llm when a call needs it.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from capabilities import registry as capability_registry
from capabilities.catalog import DATA_CLASSES
from core import llm
from core.config import SUPABASE_SCHEMA, global_supabase_client
from rbac import catalog as rbac_catalog
from rbac.service import valid_uuid

log = logging.getLogger("providers")

CACHE_SECONDS = float(os.getenv("CAPABILITY_CACHE_SECONDS", "30"))
HTTP_TIMEOUT_SECONDS = 20
MODEL_FIELDS = (
    "id,provider_id,model_id,name,enabled,available,supports_tools,supports_json_schema,context_length,"
    "max_data_class,routing,price_in,price_out,discovered_at,created_at,created_by,updated_at,updated_by"
)
# Default for a new model: OpenAI already reads the core agents' sensitive data; OpenRouter is capped
DEFAULT_MAX_DATA_CLASS = {llm.OPENAI: "sensitive", llm.OPENROUTER: "internal"}
DEFAULT_ROUTING = {"data_collection": "deny"}

# OpenRouter's "provider" request object (checked against its docs, 2026-11): field -> accepted types
ROUTING_FIELDS = {
    "order": list, "allow_fallbacks": bool, "require_parameters": bool, "data_collection": str, "zdr": bool,
    "enforce_distillable_text": bool, "only": list, "ignore": list, "quantizations": list,
    "sort": (str, dict), "preferred_min_throughput": (int, float, dict), "preferred_max_latency": (int, float, dict),
    "max_price": dict,
}


@dataclass(frozen=True)
class Provider:
    id: str
    key: str
    name: str
    base_url: str
    enabled: bool
    connection_id: str | None = None


@dataclass(frozen=True)
class Model:
    id: str
    provider: str  # provider key
    model_id: str
    name: str = ""
    enabled: bool = False
    available: bool = True
    supports_tools: bool = False
    supports_json_schema: bool = False
    context_length: int | None = None
    max_data_class: str = "internal"
    routing: dict = field(default_factory=dict)
    price_in: float | None = None
    price_out: float | None = None

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(frozen=True)
class Snapshot:
    providers: dict[str, Provider]
    models: dict[str, Model]  # by ref


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _number(value):
    return None if value is None else float(value)


# --- Snapshot ------------------------------------------------------------------------------------------

_lock = threading.Lock()
_snapshot: tuple[float, Snapshot] | None = None
_key_cache: tuple[float, str | None] | None = None


def load_snapshot() -> Snapshot:
    db = _db()
    providers = {
        r["key"]: Provider(r["id"], r["key"], r["name"], r["base_url"], bool(r["enabled"]), r.get("connection_id"))
        for r in db.table("llm_providers").select("id,key,name,base_url,enabled,connection_id").execute().data
    }
    by_id = {p.id: p.key for p in providers.values()}
    models = {}
    for r in db.table("llm_models").select(MODEL_FIELDS).execute().data:
        provider = by_id.get(r["provider_id"])
        if provider is None:
            continue
        m = Model(
            id=r["id"], provider=provider, model_id=r["model_id"], name=r.get("name") or "",
            enabled=bool(r.get("enabled")), available=bool(r.get("available", True)),
            supports_tools=bool(r.get("supports_tools")), supports_json_schema=bool(r.get("supports_json_schema")),
            context_length=r.get("context_length"), max_data_class=r.get("max_data_class") or "internal",
            routing=r.get("routing") or {}, price_in=_number(r.get("price_in")), price_out=_number(r.get("price_out")),
        )
        models[m.ref] = m
    return Snapshot(providers, models)


def snapshot() -> Snapshot:
    global _snapshot
    now = time.monotonic()
    with _lock:
        cached = _snapshot
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    fresh = load_snapshot()
    with _lock:
        _snapshot = (now, fresh)
    return fresh


def invalidate():
    global _snapshot, _key_cache
    with _lock:
        _snapshot = None
        _key_cache = None
    llm.reset_clients()


rbac_catalog.register_source("model", lambda: {ref: m.name or m.model_id for ref, m in snapshot().models.items()})


def _openrouter_key() -> str | None:
    """The decrypted OpenRouter key, cached as long as the snapshot."""
    global _key_cache
    now = time.monotonic()
    with _lock:
        cached = _key_cache
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    provider = snapshot().providers.get(llm.OPENROUTER)
    key = None
    if provider and provider.connection_id:
        secret = capability_registry.read_secret(provider.connection_id) or {}
        key = secret.get("token")
    with _lock:
        _key_cache = (now, key)
    return key


llm.register_key_resolver(llm.OPENROUTER, _openrouter_key)


def reader_max_data_class(model_ref: str) -> str | None:
    """The most restricted data a model may read: its max_data_class in the allowlist. An OpenAI model
    that isn't listed is a core agent model and reads sensitive data already; any other unlisted model
    reads nothing (None)."""
    try:
        ref = llm.parse_model(model_ref)
    except ValueError:
        return None
    model = snapshot().models.get(str(ref))
    if model is not None:
        return model.max_data_class
    return "sensitive" if ref.provider == llm.OPENAI else None


# --- Validation -------------------------------------------------------------------------------------------


def validate_routing(routing: dict) -> dict:
    """OpenRouter provider preferences. data_collection defaults to "deny" (OpenRouter's own default is
    "allow")."""
    if not isinstance(routing, dict):
        raise ValueError("routing debe ser un objeto")
    unknown = set(routing) - set(ROUTING_FIELDS)
    if unknown:
        raise ValueError(f"routing: campos desconocidos: {', '.join(sorted(unknown))}")
    for key, value in routing.items():
        if not isinstance(value, ROUTING_FIELDS[key]) or (isinstance(value, bool) and ROUTING_FIELDS[key] is not bool):
            raise ValueError(f"routing.{key}: tipo no válido")
        if isinstance(value, list) and (len(value) > 50 or not all(isinstance(v, str) and v for v in value)):
            raise ValueError(f"routing.{key}: debe ser una lista de textos")
    if routing.get("data_collection", "deny") not in ("allow", "deny"):
        raise ValueError('routing.data_collection debe ser "allow" o "deny"')
    return {**DEFAULT_ROUTING, **routing}


# --- Providers ------------------------------------------------------------------------------------------


def list_providers() -> list[dict]:
    rows = _db().table("llm_providers").select("*").order("key").execute().data
    for row in rows:
        row["has_key"] = row["key"] == llm.OPENAI or _has_secret(row.get("connection_id"))
        row["connection"] = capability_registry.get_connection(row["connection_id"]) if row.get("connection_id") else None
        row["runtime_enabled"] = row["key"] == llm.OPENAI or llm.openrouter_enabled()
    return rows


def _has_secret(connection_id) -> bool:
    if not connection_id:
        return False
    connection = capability_registry.get_connection(connection_id)
    return bool(connection and connection["has_secret"])


def get_provider(key: str) -> dict | None:
    rows = _db().table("llm_providers").select("*").eq("key", key).execute().data
    return rows[0] if rows else None


def set_provider_enabled(provider: dict, enabled: bool, by: str) -> dict:
    row = _db().table("llm_providers").update({"enabled": enabled, "updated_at": _now(), "updated_by": by}).eq(
        "id", provider["id"]).execute().data[0]
    invalidate()
    return row


def set_provider_key(provider: dict, api_key: str, owner_user_id, by: str) -> dict:
    """Stores the key encrypted in the provider's connection (created on first use). The connection's
    owner is whoever set the key (the account owner)."""
    if provider["key"] == llm.OPENAI:
        raise ValueError("La clave de OpenAI viene del entorno (LLM_SERVICE_API_KEY)")
    connection = capability_registry.get_connection(provider["connection_id"]) if provider.get("connection_id") else None
    if connection is None:
        connection = capability_registry.create_connection(None, f"llm:{provider['key']}", "token", {}, [],
                                                           owner_user_id, by)
        _db().table("llm_providers").update({"connection_id": connection["id"], "updated_at": _now(),
                                             "updated_by": by}).eq("id", provider["id"]).execute()
    connection = capability_registry.set_secret(connection, {"token": api_key}, by)
    invalidate()
    return connection


def clear_provider_key(provider: dict, by: str) -> None:
    if provider.get("connection_id"):
        capability_registry.delete_connection(provider["connection_id"])  # the provider's link is set to null
    _db().table("llm_providers").update({"connection_id": None, "updated_at": _now(), "updated_by": by}).eq(
        "id", provider["id"]).execute()
    invalidate()


def check_provider(provider: dict) -> dict:
    """OpenRouter: asks for the key's info (GET /key). Stores the result on the connection."""
    if provider["key"] != llm.OPENROUTER:
        raise ValueError("Solo se comprueba OpenRouter (OpenAI usa la clave del entorno)")
    if not provider.get("connection_id"):
        raise ValueError("Falta la clave")
    try:
        key = (capability_registry.read_secret(provider["connection_id"]) or {}).get("token")
    except Exception as e:  # SecretsUnavailable / SecretUnreadable: the message names the problem, not the key
        return capability_registry.record_check(provider["connection_id"], "error", str(e))
    if not key:
        return capability_registry.record_check(provider["connection_id"], "error", "Falta la clave")
    try:
        response = httpx.get(f"{provider['base_url'].rstrip('/')}/key", headers={"Authorization": f"Bearer {key}"},
                             timeout=HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        return capability_registry.record_check(provider["connection_id"], "error", f"Sin respuesta ({type(e).__name__})")
    if response.status_code == 401:
        status, detail = "error", "La clave no es válida"
    elif response.status_code != 200:
        status, detail = "error", f"OpenRouter respondió {response.status_code}"
    else:
        data = (response.json() or {}).get("data") or {}
        remaining = data.get("limit_remaining")
        usage = data.get("usage_monthly", data.get("usage"))
        status = "error" if remaining is not None and remaining <= 0 else "ok"
        detail = (f"Límite restante: {'sin límite' if remaining is None else remaining}; "
                  f"uso del mes: {usage if usage is not None else '?'}")
    return capability_registry.record_check(provider["connection_id"], status, detail)


# --- Models ---------------------------------------------------------------------------------------------


def _provider_keys() -> dict:
    return {r["id"]: r["key"] for r in _db().table("llm_providers").select("id,key").execute().data}


def _with_ref(row: dict, keys: dict) -> dict:
    row = dict(row)
    row["provider"] = keys.get(row["provider_id"])
    row["ref"] = f"{row['provider']}:{row['model_id']}"
    return row


def list_models(provider: str | None = None, enabled: bool | None = None, q: str | None = None,
                limit: int = 100, offset: int = 0) -> dict:
    keys = _provider_keys()
    query = _db().table("llm_models").select(MODEL_FIELDS)
    if provider:
        ids = [i for i, k in keys.items() if k == provider]
        if not ids:
            return {"total": 0, "models": []}
        query = query.eq("provider_id", ids[0])
    if enabled is not None:
        query = query.eq("enabled", enabled)
    rows = [_with_ref(r, keys) for r in query.order("model_id").execute().data]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["model_id"].lower() or needle in (r.get("name") or "").lower()]
    rows.sort(key=lambda r: (not r["enabled"], r["ref"]))
    return {"total": len(rows), "models": rows[offset: offset + max(1, min(limit, 500))]}


def get_model(model_id) -> dict | None:
    if not valid_uuid(model_id):
        return None
    rows = _db().table("llm_models").select(MODEL_FIELDS).eq("id", str(model_id)).execute().data
    return _with_ref(rows[0], _provider_keys()) if rows else None


def add_model(provider: dict, model_id: str, name: str, price_in, price_out, supports_tools: bool,
              supports_json_schema: bool, context_length, by: str) -> dict:
    """By hand (OpenAI, whose list has no prices or capabilities). Added disabled."""
    row = _db().table("llm_models").insert({
        "provider_id": provider["id"], "model_id": model_id, "name": name or model_id, "enabled": False,
        "supports_tools": supports_tools, "supports_json_schema": supports_json_schema,
        "context_length": context_length, "price_in": price_in, "price_out": price_out,
        "max_data_class": DEFAULT_MAX_DATA_CLASS[provider["key"]], "routing": {}, "created_by": by,
    }).execute().data[0]
    invalidate()
    return _with_ref(row, _provider_keys())


def update_model(model: dict, changes: dict, by: str) -> dict | None:
    allowed = {"enabled", "max_data_class", "routing", "price_in", "price_out", "supports_tools",
               "supports_json_schema", "name"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"No se puede cambiar: {', '.join(sorted(unknown))}")
    update = dict(changes)
    if "max_data_class" in update and update["max_data_class"] not in DATA_CLASSES:
        raise ValueError(f"Clase de datos desconocida: {update['max_data_class']}")
    if "routing" in update:
        update["routing"] = validate_routing(update["routing"])
    update.update({"updated_at": _now(), "updated_by": by})
    rows = _db().table("llm_models").update(update).eq("id", model["id"]).execute().data
    invalidate()
    return _with_ref(rows[0], _provider_keys()) if rows else None


def delete_model(model: dict) -> bool:
    rows = _db().table("llm_models").delete().eq("id", model["id"]).execute().data
    invalidate()
    return bool(rows)


# --- Discovery (OpenRouter) ----------------------------------------------------------------------------


def fetch_models(base_url: str) -> list[dict]:
    """OpenRouter's public model list (no key needed)."""
    response = httpx.get(f"{base_url.rstrip('/')}/models", timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json().get("data") or []


def _per_million(value) -> float | None:
    """OpenRouter prices are USD per token, as text; "-1" means variable (routers)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number < 0 else round(number * 1_000_000, 6)


def parse_model(entry: dict) -> dict | None:
    """The columns discovery owns, or None for models that don't produce text."""
    model_id = entry.get("id")
    outputs = (entry.get("architecture") or {}).get("output_modalities") or ["text"]
    if not model_id or not isinstance(model_id, str) or len(model_id) > 150 or "text" not in outputs:
        return None
    params = set(entry.get("supported_parameters") or [])
    pricing = entry.get("pricing") or {}
    context = entry.get("context_length")
    return {
        "model_id": model_id,
        "name": str(entry.get("name") or model_id)[:200],
        "supports_tools": "tools" in params,
        "supports_json_schema": "structured_outputs" in params,
        "context_length": context if isinstance(context, int) else None,
        "price_in": _per_million(pricing.get("prompt")),
        "price_out": _per_million(pricing.get("completion")),
        "available": True,
    }


def discover(provider: dict, by: str = "system") -> dict:
    """Copies the provider's model list into the allowlist. New models are added disabled (with an
    `internal` data cap and data_collection "deny"); for existing ones only the name, prices, context and
    capabilities are refreshed. Models the provider no longer lists are marked unavailable."""
    if provider["key"] != llm.OPENROUTER:
        raise ValueError("Solo OpenRouter publica su lista de modelos con precios; añade los de OpenAI a mano")
    found = [m for m in (parse_model(e) for e in fetch_models(provider["base_url"])) if m]
    db = _db()
    existing = {r["model_id"]: r for r in db.table("llm_models").select("id,model_id,available")
                .eq("provider_id", provider["id"]).execute().data}
    now = _now()
    new = [m for m in found if m["model_id"] not in existing]
    for start in range(0, len(found), 200):
        # merge-duplicates updates only these columns: enabled, max_data_class and routing stay as they are
        db.table("llm_models").upsert(
            [{**m, "provider_id": provider["id"], "discovered_at": now} for m in found[start:start + 200]],
            on_conflict="provider_id,model_id",
            default_to_null=False,  # new rows take the column defaults (disabled...), not NULL
        ).execute()
    # New rows take the column defaults: disabled, max_data_class 'internal', data_collection "deny"
    gone = [r["id"] for mid, r in existing.items() if mid not in {m["model_id"] for m in found} and r["available"]]
    for start in range(0, len(gone), 200):
        db.table("llm_models").update({"available": False}).in_("id", gone[start:start + 200]).execute()
    invalidate()
    return {"found": len(found), "added": len(new), "unavailable": len(gone)}
