"""Local Codex token accounting and API-equivalent cost estimates."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from local_codex import TokenUsageRecord


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


# Standard USD rates for ChatGPT Work/Codex token-based usage, as of 2026-11-06.
# Unknown model IDs remain visible as unpriced instead of being assigned a guess.
MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-6-astra": ModelPricing(10.00, 1.00, 50.00),
    "gpt-6.1-sol": ModelPricing(2.00, 0.10, 10.00),
    "gpt-6.1-terra": ModelPricing(1.00, 0.05, 5.00),
    "gpt-6-sol": ModelPricing(2.00, 0.20, 10.00),
    "gpt-6-luna": ModelPricing(0.10, 0.01, 0.50),
    "gpt-5.6-sol": ModelPricing(4.00, 0.40, 20.00),
    "gpt-5.6-terra": ModelPricing(2.00, 0.20, 12.00),
    "gpt-5.6-luna": ModelPricing(0.20, 0.02, 1.20),
    "gpt-5.5": ModelPricing(5.00, 0.50, 30.00),
    "gpt-5.4": ModelPricing(2.50, 0.25, 15.00),
    "gpt-5.4-mini": ModelPricing(0.75, 0.075, 4.50),
    "gpt-5.3-codex": ModelPricing(1.75, 0.175, 14.00),
    "gpt-5.2": ModelPricing(1.75, 0.175, 14.00),
}


@dataclass(frozen=True)
class ModelCostRow:
    model: str
    cost: Optional[float]
    share: Optional[float]
    processed_tokens: int
    token_share: Optional[float]


@dataclass(frozen=True)
class CostAnalytics:
    total_cost: float
    input_cost: float
    cached_input_cost: float
    output_cost: float
    cache_savings: float
    processed_tokens: int
    uncached_input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    sessions: int
    responses: int
    daily_costs: tuple[float, ...]
    daily_tokens: tuple[int, ...]
    chart_labels: tuple[str, str]
    model_rows: tuple[ModelCostRow, ...]
    unpriced_models: tuple[str, ...]


def _parse_timestamp(value: str) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _model_pricing(model: str) -> Optional[ModelPricing]:
    normalized = model.casefold().strip()
    for model_id in sorted(MODEL_PRICING, key=len, reverse=True):
        if normalized == model_id or normalized.startswith(model_id + "-"):
            return MODEL_PRICING[model_id]
    return None


def calculate_cost_analytics(
    records: tuple[TokenUsageRecord, ...], days: int,
    now: Optional[datetime] = None,
) -> CostAnalytics:
    """Aggregate local response token records for one rolling time window."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(days=days)
    local_now = current.astimezone()
    priced_cost = input_cost = cached_cost = output_cost = cache_savings = 0.0
    processed = uncached_input = cached_input = cache_write = output_tokens = 0
    sessions: set[str] = set()
    model_totals: dict[str, dict[str, float | int | bool]] = defaultdict(
        lambda: {"cost": 0.0, "tokens": 0, "priced": False}
    )
    unpriced: set[str] = set()

    if days == 1:
        first_hour = local_now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        buckets = [first_hour + timedelta(hours=index) for index in range(24)]
        bucket_keys = {value: index for index, value in enumerate(buckets)}
        labels = (buckets[0].strftime("%H:%M"), buckets[-1].strftime("%H:%M"))
    else:
        last_day = local_now.date()
        first_day = last_day - timedelta(days=days - 1)
        buckets = [first_day + timedelta(days=index) for index in range(days)]
        bucket_keys = {value: index for index, value in enumerate(buckets)}
        labels = (buckets[0].strftime("%b %d"), buckets[-1].strftime("%b %d"))

    daily_costs = [0.0] * len(buckets)
    daily_tokens = [0] * len(buckets)
    responses = 0
    for record in records:
        timestamp = _parse_timestamp(record.timestamp)
        if timestamp is None or timestamp < cutoff or timestamp > current + timedelta(minutes=5):
            continue
        local_time = timestamp.astimezone()
        bucket = local_time.replace(minute=0, second=0, microsecond=0) if days == 1 else local_time.date()
        index = bucket_keys.get(bucket)
        if index is None:
            continue

        input_total = max(0, record.input_tokens)
        cached = min(input_total, max(0, record.cached_input_tokens))
        cache_write_count = min(input_total - cached, max(0, record.cache_write_input_tokens))
        uncached = max(0, input_total - cached - cache_write_count)
        generated = max(0, record.output_tokens)
        token_total = input_total + generated
        responses += 1
        if record.session_id:
            sessions.add(record.session_id)
        processed += token_total
        uncached_input += uncached
        cached_input += cached
        cache_write += cache_write_count
        output_tokens += generated
        daily_tokens[index] += token_total
        row = model_totals[record.model or "Unknown model"]
        row["tokens"] = int(row["tokens"]) + token_total
        pricing = _model_pricing(record.model)
        if pricing is None:
            unpriced.add(record.model or "Unknown model")
            continue

        uncached_cost = uncached * pricing.input_per_million / 1_000_000
        cache_cost = cached * pricing.cached_input_per_million / 1_000_000
        generated_cost = generated * pricing.output_per_million / 1_000_000
        cost = uncached_cost + cache_cost + generated_cost
        input_cost += uncached_cost
        cached_cost += cache_cost
        output_cost += generated_cost
        priced_cost += cost
        cache_savings += (
            cached * (pricing.input_per_million - pricing.cached_input_per_million)
            + cache_write_count * pricing.input_per_million
        ) / 1_000_000
        daily_costs[index] += cost
        row["cost"] = float(row["cost"]) + cost
        row["priced"] = True

    model_rows = []
    for model, totals in model_totals.items():
        is_priced = bool(totals["priced"])
        cost = float(totals["cost"]) if is_priced else None
        share = (cost / priced_cost * 100) if cost is not None and priced_cost > 0 else None
        token_total = int(totals["tokens"])
        token_share = token_total / processed * 100 if processed else None
        model_rows.append(ModelCostRow(model, cost, share, token_total, token_share))
    model_rows.sort(key=lambda row: (row.cost is None, -(row.cost or 0.0), row.model.casefold()))

    return CostAnalytics(
        total_cost=priced_cost,
        input_cost=input_cost,
        cached_input_cost=cached_cost,
        output_cost=output_cost,
        cache_savings=cache_savings,
        processed_tokens=processed,
        uncached_input_tokens=uncached_input,
        cached_input_tokens=cached_input,
        cache_write_tokens=cache_write,
        output_tokens=output_tokens,
        sessions=len(sessions),
        responses=responses,
        daily_costs=tuple(daily_costs),
        daily_tokens=tuple(daily_tokens),
        chart_labels=labels,
        model_rows=tuple(model_rows),
        unpriced_models=tuple(sorted(unpriced, key=str.casefold)),
    )
