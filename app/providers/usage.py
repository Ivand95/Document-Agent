"""LLM usage: one row per call (tokens and cost, never the content), and spend per user and month.

core.llm reports every call here. Rows are written by a background thread so a slow database never
delays an answer; a failed write is logged and dropped. The cost is OpenRouter's own figure when it
gives one (usage.cost, in credits = USD), otherwise tokens × the model's prices in the allowlist,
otherwise unknown (null).
"""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from core import llm
from core.config import SUPABASE_SCHEMA, global_supabase_client
from providers import registry

log = logging.getLogger("providers")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-usage")
SYNCHRONOUS = False  # tests write at once
PAGE = 1000


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def price_cost(model_ref: str, tokens_in: int, tokens_out: int) -> float | None:
    try:
        model = registry.snapshot().models.get(model_ref)
    except Exception:
        return None
    if model is None or model.price_in is None or model.price_out is None:
        return None
    return round((tokens_in * model.price_in + tokens_out * model.price_out) / 1_000_000, 8)


def store(record: dict) -> None:
    row = dict(record)
    if row.get("cost_usd") is None:
        cost = price_cost(row["model"], row["tokens_in"], row["tokens_out"])
        if cost is not None:
            row["cost_usd"], row["cost_source"] = cost, "price_list"
    try:
        _db().table("llm_usage").insert(row).execute()
    except Exception:
        log.exception("Could not store LLM usage for %s", row.get("model"))


def sink(record: dict) -> None:
    if SYNCHRONOUS:
        store(record)
    else:
        _executor.submit(store, record)


llm.register_usage_sink(sink)


def month_start(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _rows(fields: str, since: datetime, until: datetime | None = None, **eq) -> list[dict]:
    rows, offset = [], 0
    while True:
        query = _db().table("llm_usage").select(fields).gte("at", since.isoformat())
        if until is not None:
            query = query.lt("at", until.isoformat())
        for column, value in eq.items():
            query = query.eq(column, str(value))
        page = query.order("at").range(offset, offset + PAGE - 1).execute().data
        rows += page
        if len(page) < PAGE:
            return rows
        offset += PAGE


def month_spend(user_id, provider: str, now: datetime | None = None) -> float:
    """USD spent this calendar month (UTC) by the user on the provider. Calls with an unknown cost count 0."""
    rows = _rows("cost_usd", month_start(now), user_id=user_id, provider=provider)
    return round(sum(float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None), 8)


GROUPS = ("user_id", "model", "agent_key", "capability_key", "provider")


def summary(month: datetime, group_by: str) -> list[dict]:
    """Calls, tokens and cost of the month, per group; most expensive first."""
    if group_by not in GROUPS:
        raise ValueError(f"group_by debe ser uno de: {', '.join(GROUPS)}")
    start = month_start(month)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    totals: dict = defaultdict(lambda: {"calls": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                                        "unpriced_calls": 0})
    for row in _rows(f"{group_by},tokens_in,tokens_out,cost_usd,outcome", start, end):
        t = totals[row.get(group_by)]
        t["calls"] += 1
        t["errors"] += row.get("outcome") == "error"
        t["tokens_in"] += row.get("tokens_in") or 0
        t["tokens_out"] += row.get("tokens_out") or 0
        if row.get("cost_usd") is None:
            t["unpriced_calls"] += 1
        else:
            t["cost_usd"] += float(row["cost_usd"])
    result = [{group_by: key, **{k: round(v, 6) if k == "cost_usd" else v for k, v in t.items()}}
              for key, t in totals.items()]
    return sorted(result, key=lambda r: r["cost_usd"], reverse=True)
