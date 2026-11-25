"""The one path admin-built capabilities (prompt tools, code tools, skills) use to call a model.

A call runs only if:
  1. the model is in the allowlist, enabled and still offered by its provider;
  2. the provider is enabled (and, for OpenRouter, OPENROUTER_ENABLED=true and a key is stored);
  3. RBAC allows `use` on the model (resource_type "model", id "<provider>:<model_id>");
  4. the data the call carries is at or below the model's max_data_class (sensitive data never reaches an
     `internal` model: that is every OpenRouter model unless a super_admin raised it);
  5. the model supports what the call needs (tools, JSON schema);
  6. for OpenRouter, the user's spend this month is under the budget their permission sets
     (constraints.llm_budget_usd_month; no budget set = no cap yet).
The OpenRouter routing preferences of the model are sent with every request. A failed call is retried
once. Usage is recorded for every attempt. The core agents don't come through here: they call core.llm,
which only accepts OpenAI models.
"""
import asyncio
import logging
from dataclasses import dataclass

from capabilities.catalog import data_class_rank
from core import llm
from providers import registry, usage
from providers.registry import Model
from rbac.model import Access

log = logging.getLogger("providers")

DEFAULT_TIMEOUT_SECONDS = 60
BUDGET = "llm_budget_usd_month"

MESSAGES = {
    "not_found": "Ese modelo no está en la lista de modelos permitidos.",
    "disabled": "Ese modelo está desactivado.",
    "unavailable": "Ese modelo no está disponible ahora.",
    "denied": "No tienes permiso para usar ese modelo.",
    "data_class": "Ese modelo no puede recibir este tipo de datos.",
    "no_tools": "Ese modelo no admite herramientas.",
    "no_json_schema": "Ese modelo no admite respuestas con esquema JSON.",
    "budget": "Se alcanzó el presupuesto mensual de modelos para tu rol.",
    "failed": "El modelo no respondió. Intenta de nuevo más tarde.",
}


class ModelError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.message = MESSAGES.get(code, MESSAGES["failed"])
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ChatResult:
    model: str
    content: str | None
    tool_calls: list
    tokens_in: int
    tokens_out: int
    cost_usd: float | None


@dataclass(frozen=True)
class Authorized:
    model: Model
    budget: float | None


def authorize(access: Access, model_ref: str, data_class: str, needs_tools: bool = False,
              needs_json_schema: bool = False, snap=None) -> Authorized:
    """Checks 1–5 (and finds the budget for 6). Raises ModelError."""
    snap = snap or registry.snapshot()
    try:
        ref = str(llm.parse_model(model_ref))
    except ValueError as e:
        raise ModelError("not_found", str(e)) from None
    model = snap.models.get(ref)
    if model is None:
        raise ModelError("not_found", ref)
    if not model.enabled:
        raise ModelError("disabled", ref)
    provider = snap.providers.get(model.provider)
    if not model.available or provider is None or not provider.enabled or (
            model.provider == llm.OPENROUTER and not llm.openrouter_enabled()):
        raise ModelError("unavailable", ref)
    decision = access.check("use", "model", ref)
    if not decision.allowed:
        raise ModelError("denied", decision.reason)
    if data_class_rank(data_class) > data_class_rank(model.max_data_class):
        raise ModelError("data_class", f"{data_class} > {model.max_data_class}")
    if needs_tools and not model.supports_tools:
        raise ModelError("no_tools", ref)
    if needs_json_schema and not model.supports_json_schema:
        raise ModelError("no_json_schema", ref)
    budget = decision.constraints.get(BUDGET)
    return Authorized(model, float(budget) if isinstance(budget, (int, float)) else None)


def check_budget(access: Access, authorized: Authorized) -> None:
    """Check 6. Only OpenRouter calls are capped (OpenAI spend is tracked, not capped)."""
    if authorized.budget is None or authorized.model.provider != llm.OPENROUTER:
        return
    spent = usage.month_spend(access.user_id, llm.OPENROUTER)
    if spent >= authorized.budget:
        raise ModelError("budget", f"{spent:.4f} >= {authorized.budget}")


def models_for(access: Access, data_class: str, needs_tools: bool = False, needs_json_schema: bool = False) -> list[str]:
    """The models this user may use for this kind of data (for model pickers)."""
    snap = registry.snapshot()
    allowed = []
    for ref in sorted(snap.models):
        try:
            authorize(access, ref, data_class, needs_tools, needs_json_schema, snap)
        except ModelError:
            continue
        allowed.append(ref)
    return allowed


async def chat(access: Access, model_ref: str, messages: list, data_class: str, *, tools: list | None = None,
               json_schema: dict | None = None, strict: bool = True, max_tokens: int | None = None,
               agent_key: str | None = None, capability_key: str | None = None,
               timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ChatResult:
    """One chat completion on an allowlisted model. `data_class` is the most restricted class of anything
    in `messages` (the caller knows where the content came from). `strict` asks the provider to enforce
    `json_schema` exactly (it must then close every object). Raises ModelError."""
    authorized = authorize(access, model_ref, data_class, bool(tools), json_schema is not None)
    check_budget(access, authorized)
    model = authorized.model
    kwargs: dict = {"messages": messages}
    if tools:
        kwargs["tools"] = tools
    if json_schema is not None:
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "result", "strict": strict, "schema": json_schema}}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if model.provider == llm.OPENROUTER:
        kwargs["extra_body"] = {"provider": registry.validate_routing(model.routing)}

    last_error = None
    with llm.usage_context(user_id=access.user_id, agent_key=agent_key, capability_key=capability_key):
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(llm._create, model.provider, model.model_id, **kwargs), timeout=timeout
                )
                break
            except llm.ProviderUnavailable as e:
                raise ModelError("unavailable", str(e)) from None
            except Exception as e:  # timeouts, HTTP and provider errors: one retry
                last_error = e
                log.warning("Model %s failed (attempt %d): %s", model.ref, attempt + 1, type(e).__name__)
        else:
            raise ModelError("failed", type(last_error).__name__)

    message = response.choices[0].message
    used = getattr(response, "usage", None)
    cost = getattr(used, "cost", None)
    tokens_in = int(getattr(used, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(used, "completion_tokens", 0) or 0)
    if not isinstance(cost, (int, float)):
        cost = usage.price_cost(model.ref, tokens_in, tokens_out)
    return ChatResult(model=model.ref, content=message.content, tool_calls=list(message.tool_calls or []),
                      tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost)
