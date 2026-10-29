import asyncio
import json

import openai

from config import CHAT_MODEL, LIGHT_MODEL, LLM_API_KEY, LLM_SERVICE

_client = None


def client() -> openai.OpenAI:
    global _client
    if LLM_SERVICE != "openai":
        raise RuntimeError("Chat generation requires LLM_SERVICE=openai")
    if _client is None:
        _client = openai.OpenAI(api_key=LLM_API_KEY)
    return _client


def chat_json_sync(messages, name, schema, model=CHAT_MODEL, retries=1):
    """Chat completion constrained to a JSON schema (structured outputs).

    Retries once if the reply isn't parseable (e.g. truncated or refused).
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }
    last_error = None
    for _ in range(retries + 1):
        response = client().chat.completions.create(
            model=model, messages=messages, response_format=response_format
        )
        try:
            return json.loads(response.choices[0].message.content)
        except (TypeError, json.JSONDecodeError) as e:
            last_error = e
    raise ValueError(f"LLM did not return valid JSON: {last_error}")


def chat_text_sync(messages, model=LIGHT_MODEL):
    response = client().chat.completions.create(model=model, messages=messages)
    return (response.choices[0].message.content or "").strip()


async def chat_json(messages, name, schema, model=CHAT_MODEL):
    return await asyncio.to_thread(chat_json_sync, messages, name, schema, model)


async def chat_text(messages, model=LIGHT_MODEL):
    return await asyncio.to_thread(chat_text_sync, messages, model)
