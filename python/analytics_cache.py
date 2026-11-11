"""Small persistent cache of local token counters for instant Analytics startup."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import get_cache_dir
from local_codex import (
    TokenUsageRecord,
    _TOKEN_USAGE_CACHE_MAX_RECORDS,
    _TOKEN_USAGE_RETENTION_DAYS,
    _as_datetime,
)


CACHE_FILE_NAME = "token-usage-analytics.json"


def _cache_file() -> Path:
    return get_cache_dir() / CACHE_FILE_NAME


def save_cached_token_usage_records(records: tuple[TokenUsageRecord, ...]) -> None:
    """Persist only recent, non-sensitive accounting fields via atomic replace."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_TOKEN_USAGE_RETENTION_DAYS)
    recent = [
        record for record in records
        if (stamp := _as_datetime(record.timestamp)) is not None and stamp >= cutoff
    ][-_TOKEN_USAGE_CACHE_MAX_RECORDS:]
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": [
            {
                "timestamp": record.timestamp,
                "session_id": record.session_id,
                "model": record.model,
                "input_tokens": record.input_tokens,
                "cached_input_tokens": record.cached_input_tokens,
                "cache_write_input_tokens": record.cache_write_input_tokens,
                "output_tokens": record.output_tokens,
            }
            for record in recent
        ],
    }
    cache_file = _cache_file()
    temporary = cache_file.with_name(
        "{}.{}.tmp".format(cache_file.name, os.getpid()),
    )
    try:
        with temporary.open("w", encoding="utf-8") as cache_stream:
            json.dump(payload, cache_stream, separators=(",", ":"))
        os.replace(temporary, cache_file)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_cached_token_usage_records() -> Optional[tuple[TokenUsageRecord, ...]]:
    """Read the last tray-generated Analytics data; None means no cache exists."""
    cache_file = _cache_file()
    try:
        with cache_file.open("r", encoding="utf-8") as cache_stream:
            payload = json.load(cache_stream)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=_TOKEN_USAGE_RETENTION_DAYS)
    records = []
    for item in payload["records"][-_TOKEN_USAGE_CACHE_MAX_RECORDS:]:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("timestamp")
        session_id = item.get("session_id")
        model = item.get("model")
        stamp = _as_datetime(timestamp)
        if (
            not isinstance(timestamp, str)
            or stamp is None
            or stamp < cutoff
            or not isinstance(session_id, str)
            or not isinstance(model, str)
        ):
            continue
        counts = []
        for name in (
            "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
        ):
            value = item.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                counts = []
                break
            counts.append(value)
        if len(counts) != 4:
            continue
        records.append(TokenUsageRecord(
            timestamp=timestamp,
            session_id=session_id,
            model=model,
            input_tokens=counts[0],
            cached_input_tokens=counts[1],
            cache_write_input_tokens=counts[2],
            output_tokens=counts[3],
        ))
    records.sort(key=lambda item: item.timestamp)
    return tuple(records)
