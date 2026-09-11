"""Token usage and estimated cost per conversation.

Every model request the agent makes is metered by `UsageMeter` (a pydantic-ai
capability attached to each run, next to the tool-event bridge): provider-
reported token counts plus the list-price cost from `genai-prices` (bundled
with pydantic-ai; it knows cached-input discounts). Records are appended to
<data_root>/<profile>/state/threads/<id>/usage.jsonl and published on the per-thread SSE
hub so the UI's counter ticks live.

Conversations that ran before metering existed can carry records flagged
`estimated: true` (reconstructed from the stored transcript with tiktoken —
see the one-off backfill); `summary()` reports how many requests are exact
vs estimated so the UI can label the figure honestly.

Pricing for models genai-prices does not know comes from the profile's
[model.prices] table (JSON-ish TOML: {"gpt-6-astra": {"input": 2.0, "output": 8.0,
"cache_read": 0.5}} in USD per million tokens); otherwise the cost is null and
the UI shows tokens only.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities.abstract import AbstractCapability

from .bridge import hub, safe_id

try:
    from genai_prices import Usage as _PriceUsage
    from genai_prices import calc_price as _calc_price
except ImportError:  # pragma: no cover - genai-prices ships with pydantic-ai
    _PriceUsage = None
    _calc_price = None


def _strip_provider(model: str) -> str:
    """'openai:gpt-5.6-luna' -> 'gpt-5.6-luna'."""
    return model.split(":", 1)[1] if ":" in model else model


def _provider(model: str) -> str | None:
    return model.split(":", 1)[0] if ":" in model else None


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    overrides: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """USD list price for one request, or None when the model is unknown.

    `input_tokens` is the provider's total input count (cached tokens
    included, as OpenAI reports it); the cached part is billed at the
    cache-read rate when the price table has one.
    """
    name = _strip_provider(model)
    override = (overrides or {}).get(name) or (overrides or {}).get(model)
    if override:
        uncached = max(input_tokens - cache_read_tokens, 0)
        per_m = 1 / 1_000_000
        return (
            uncached * override.get("input", 0.0) * per_m
            + cache_read_tokens * override.get("cache_read", override.get("input", 0.0)) * per_m
            + output_tokens * override.get("output", 0.0) * per_m
        )
    if _calc_price is None:
        return None
    try:
        calc = _calc_price(
            _PriceUsage(
                input_tokens=input_tokens,
                cache_read_tokens=cache_read_tokens,
                output_tokens=output_tokens,
            ),
            model_ref=name,
            provider_id=_provider(model),
        )
    except Exception:
        return None
    return float(calc.total_price)


def usage_path(threads_dir: Path, thread_id: str) -> Path:
    return threads_dir / safe_id(thread_id) / "usage.jsonl"


def append_record(threads_dir: Path, thread_id: str, record: dict) -> None:
    path = usage_path(threads_dir, thread_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # metering must never break a run
        print(f"[usage] write failed: {exc!r}", file=sys.stderr)


def read_records(threads_dir: Path, thread_id: str) -> list[dict]:
    path = usage_path(threads_dir, thread_id)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summary(threads_dir: Path, thread_id: str) -> dict:
    """Whole-conversation totals: tokens, cost, exact vs estimated requests,
    per-model breakdown, and the ts of the last stored record (so a live
    subscriber can tell already-counted records from new ones)."""
    totals = {
        "requests": 0,
        "estimated_requests": 0,
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "cost_known": True,  # False when any record lacks a price
        "models": {},
        "last_ts": 0.0,
    }
    for r in read_records(threads_dir, thread_id):
        n = int(r.get("requests", 1))
        totals["requests"] += n
        if r.get("estimated"):
            totals["estimated_requests"] += n
        totals["input_tokens"] += int(r.get("input_tokens", 0))
        totals["cache_read_tokens"] += int(r.get("cache_read_tokens", 0))
        totals["output_tokens"] += int(r.get("output_tokens", 0))
        totals["reasoning_tokens"] += int(r.get("reasoning_tokens", 0))
        cost = r.get("cost_usd")
        if cost is None:
            totals["cost_known"] = False
        else:
            totals["cost_usd"] += float(cost)
        model = r.get("model") or "?"
        m = totals["models"].setdefault(
            model, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        m["requests"] += n
        m["input_tokens"] += int(r.get("input_tokens", 0))
        m["output_tokens"] += int(r.get("output_tokens", 0))
        if cost is not None:
            m["cost_usd"] += float(cost)
        totals["last_ts"] = max(totals["last_ts"], float(r.get("ts", 0) or 0))
    return totals


class UsageMeter(AbstractCapability):
    """Record every model request of a run: tokens + list-price cost."""

    def __init__(
        self,
        threads_dir: Path,
        thread_id: str,
        *,
        price_overrides: dict[str, dict[str, float]] | None = None,
    ):
        super().__init__()
        self._threads_dir = threads_dir
        self._thread_id = thread_id
        self._overrides = price_overrides or {}

    async def after_model_request(self, ctx, *, request_context, response):
        try:
            self._record(ctx, response)
        except Exception as exc:  # never let metering break the run
            print(f"[usage] record failed: {exc!r}", file=sys.stderr)
        return response

    def _record(self, ctx, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        model = getattr(response, "model_name", None) or getattr(ctx.model, "model_name", None) or str(ctx.model)
        provider = getattr(response, "provider_name", None) or _provider(str(ctx.model))
        model_ref = f"{provider}:{model}" if provider and ":" not in model else model
        details = getattr(usage, "details", None) or {}
        record: dict[str, Any] = {
            "ts": time.time(),
            "run_id": getattr(ctx, "run_id", None),
            "model": model_ref,
            "requests": 1,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(usage, "cache_write_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "reasoning_tokens": int(details.get("reasoning_tokens", 0) or 0),
            "estimated": False,
        }
        # Prefer pydantic-ai's own price calculation (same table, knows the
        # provider); fall back to ours (which also knows the overrides).
        cost: float | None = None
        try:
            cost = float(response.cost().total_price)
        except Exception:
            cost = None
        if cost is None:
            cost = estimate_cost(
                model_ref,
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
                cache_read_tokens=record["cache_read_tokens"],
                overrides=self._overrides,
            )
        record["cost_usd"] = cost
        append_record(self._threads_dir, self._thread_id, record)
        hub.publish(self._thread_id, {"phase": "usage", **record}, persist=False)
