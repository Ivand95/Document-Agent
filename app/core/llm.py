"""LLM providers and the chat calls of the core agents.

Models are named `provider:model` (`openai:gpt-4o`, `openrouter:anthropic/claude-…`); a name without a
known provider prefix is an OpenAI model, so today's settings (`gpt-4o`, `ft:gpt-4o:…`) keep working.

- OpenAI serves the core agents (documents, audio: guard, rewrite, rerank, generate). chat_json/chat_text
  only accept OpenAI models: their data is sensitive and their strict JSON-schema calls aren't supported
  by every OpenRouter model.
- OpenRouter is OpenAI-compatible (same SDK, its own base URL). It is off unless OPENROUTER_ENABLED=true,
  and its key comes from a registered resolver (the encrypted connection, WP4b), never from the code.
  Only admin-built capabilities will call it, through the gateway's data-class and budget checks.
"""
import asyncio
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

import openai

from core.config import CHAT_MODEL, LIGHT_MODEL, LLM_API_KEY, LLM_SERVICE

OPENAI = "openai"
OPENROUTER = "openrouter"
PROVIDERS = (OPENAI, OPENROUTER)

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class ProviderUnavailable(RuntimeError):
    """The provider is switched off or has no key."""


def openrouter_enabled() -> bool:
    return os.getenv("OPENROUTER_ENABLED", "false").lower() == "true"


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_model(ref: str) -> ModelRef:
    """'openrouter:vendor/model' -> (openrouter, vendor/model); 'gpt-4o' or 'openai:gpt-4o' -> (openai, gpt-4o)."""
    ref = (ref or "").strip()
    prefix, sep, rest = ref.partition(":")
    if sep and prefix in PROVIDERS:
        if not rest.strip():
            raise ValueError(f"Falta el modelo en '{ref}'")
        return ModelRef(prefix, rest.strip())
    if not ref:
        raise ValueError("Falta el modelo")
    return ModelRef(OPENAI, ref)


# provider -> function returning its API key (OpenRouter: registered by the model registry, WP4b)
_key_resolvers: dict[str, Callable[[], str | None]] = {}
_clients: dict[str, tuple[str, openai.OpenAI]] = {}  # provider -> (key it was built with, client)
_lock = threading.Lock()


def register_key_resolver(provider: str, resolver: Callable[[], str | None]) -> None:
    if provider not in PROVIDERS or provider == OPENAI:
        raise ValueError(f"No se registra la clave de {provider}")
    _key_resolvers[provider] = resolver
    reset_clients()


def reset_clients() -> None:
    with _lock:
        _clients.clear()


def _settings(provider: str) -> tuple[str, dict]:
    """(api key, extra client arguments) of the provider, or ProviderUnavailable."""
    if provider == OPENAI:
        if LLM_SERVICE != "openai":
            raise ProviderUnavailable("Chat generation requires LLM_SERVICE=openai")
        return LLM_API_KEY, {}
    if provider == OPENROUTER:
        if not openrouter_enabled():
            raise ProviderUnavailable("OpenRouter está desactivado (OPENROUTER_ENABLED)")
        resolver = _key_resolvers.get(OPENROUTER)
        key = resolver() if resolver else None
        if not key:
            raise ProviderUnavailable("OpenRouter no tiene clave configurada")
        return key, {"base_url": OPENROUTER_BASE_URL}
    raise ProviderUnavailable(f"Proveedor desconocido: {provider}")


def client(provider: str = OPENAI) -> openai.OpenAI:
    key, extra = _settings(provider)
    with _lock:
        cached = _clients.get(provider)
        if cached and cached[0] == key:
            return cached[1]
        fresh = openai.OpenAI(api_key=key, **extra)
        _clients[provider] = (key, fresh)
        return fresh


def core_model(model: str) -> str:
    """The OpenAI model name for a core-agent call; refuses any other provider."""
    ref = parse_model(model)
    if ref.provider != OPENAI:
        raise ValueError(f"Los agentes principales solo usan modelos de OpenAI, no '{model}'")
    return ref.model


def chat_json_sync(messages, name, schema, model=CHAT_MODEL, retries=1):
    """Chat completion constrained to a JSON schema (structured outputs).

    Retries once if the reply isn't parseable (e.g. truncated or refused).
    """
    model = core_model(model)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }
    last_error = None
    for _ in range(retries + 1):
        response = client(OPENAI).chat.completions.create(
            model=model, messages=messages, response_format=response_format
        )
        try:
            return json.loads(response.choices[0].message.content)
        except (TypeError, json.JSONDecodeError) as e:
            last_error = e
    raise ValueError(f"LLM did not return valid JSON: {last_error}")


def chat_text_sync(messages, model=LIGHT_MODEL):
    model = core_model(model)
    response = client(OPENAI).chat.completions.create(model=model, messages=messages)
    return (response.choices[0].message.content or "").strip()


async def chat_json(messages, name, schema, model=CHAT_MODEL):
    return await asyncio.to_thread(chat_json_sync, messages, name, schema, model)


async def chat_text(messages, model=LIGHT_MODEL):
    return await asyncio.to_thread(chat_text_sync, messages, model)
