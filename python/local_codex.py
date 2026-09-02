"""Read-only summaries of local Codex metadata for the dashboard.

This module deliberately never reads prompts, messages, or history text.  It only
uses thread metadata from Codex's SQLite state database and rate-limit event
metadata from rollout JSONL files.
"""
from __future__ import annotations

import json
import msvcrt
import sqlite3
import os
from contextlib import contextmanager
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


CODEX_DIR = Path.home() / ".codex"
SNAPSHOT_FILE = Path.home() / "AppData" / "Local" / "AI-Usage-Tray" / "codex-snapshots.jsonl"


@dataclass(frozen=True)
class RolloutWindow:
    """A rate-limit observation recorded by Codex locally, not current usage."""

    used_percent: Optional[float]
    resets_at: Optional[str]
    window_minutes: Optional[int]


@dataclass(frozen=True)
class LocalCodexSnapshot:
    """Safe, aggregate local activity data for presentation."""

    session_count: int = 0
    sessions_last_24h: int = 0
    sessions_last_7d: int = 0
    tokens_used: Optional[int] = None
    model_breakdown: dict[str, int] = field(default_factory=dict)
    last_updated_at: Optional[str] = None
    observed_primary: Optional[RolloutWindow] = None
    observed_secondary: Optional[RolloutWindow] = None
    observed_plan_type: Optional[str] = None
    observed_credits: Optional[int] = None


@dataclass(frozen=True)
class UsageSnapshot:
    timestamp: str
    five_hour: Optional[float]
    seven_day: Optional[float]
    tokens_used: Optional[int]
    session_count: int


def _as_datetime(value: Any) -> Optional[datetime]:
    """Normalise common Codex timestamp encodings without raising."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            try:
                return _as_datetime(float(value))
            except ValueError:
                return None
    return None


def _format_timestamp(value: Any) -> Optional[str]:
    timestamp = _as_datetime(value)
    return timestamp.isoformat() if timestamp else None


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _window_from_mapping(value: Any) -> Optional[RolloutWindow]:
    if not isinstance(value, dict):
        return None
    raw_percent = _first(value, "used_percent", "usedPercent")
    raw_minutes = _first(value, "window_minutes", "windowMinutes")
    try:
        used_percent = float(raw_percent) if raw_percent is not None else None
    except (TypeError, ValueError):
        used_percent = None
    try:
        window_minutes = int(raw_minutes) if raw_minutes is not None else None
    except (TypeError, ValueError):
        window_minutes = None
    reset = _format_timestamp(_first(value, "resets_at", "reset_at", "resetsAt", "resetAt"))
    if used_percent is None and reset is None and window_minutes is None:
        return None
    return RolloutWindow(used_percent, reset, window_minutes)


def parse_rate_limits_event(event: dict[str, Any]) -> tuple[Optional[RolloutWindow], Optional[RolloutWindow], Optional[str], Optional[int]]:
    """Extract documented rate-limit metadata from a rollout JSONL event."""
    payload = event.get("payload", event)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None, None, None, None
    if not isinstance(payload, dict):
        return None, None, None, None

    rate_limits = payload.get("rate_limits", payload.get("rateLimit", {}))
    if not isinstance(rate_limits, dict):
        return None, None, None, None
    primary = _window_from_mapping(_first(rate_limits, "primary", "primary_window", "primaryWindow"))
    secondary = _window_from_mapping(_first(rate_limits, "secondary", "secondary_window", "secondaryWindow"))
    plan_type = _first(payload, "plan_type", "planType")
    credits_value = _first(payload, "credits", "reset_credits", "rate_limit_reset_credits")
    if isinstance(credits_value, dict):
        credits_value = _first(credits_value, "available_count", "availableCount")
    try:
        credits = int(credits_value) if credits_value is not None else None
    except (TypeError, ValueError):
        credits = None
    return primary, secondary, str(plan_type) if plan_type is not None else None, credits


def _iter_rollout_events(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            continue
        try:
            with path.open("r", encoding="utf-8") as rollout_file:
                for line in rollout_file:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("type", event.get("event_type")) == "event_msg":
                        yield event
        except (OSError, UnicodeDecodeError):
            continue


def _read_threads(database_path: Path) -> list[dict[str, Any]]:
    if not database_path.is_file():
        return []
    try:
        connection = sqlite3.connect("file:{}?mode=ro".format(database_path.as_posix()), uri=True)
        connection.row_factory = sqlite3.Row
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            required = {"created_at", "updated_at", "tokens_used", "model", "rollout_path"}
            selected = [column for column in required if column in columns]
            if not selected:
                return []
            rows = [dict(row) for row in connection.execute("SELECT {} FROM threads".format(", ".join(selected)))]
        finally:
            connection.close()
    except sqlite3.Error:
        return []

    return rows


def read_local_codex_snapshot(codex_dir: Optional[Path] = None, now: Optional[datetime] = None) -> LocalCodexSnapshot:
    """Build an aggregate dashboard snapshot from files below ``Path.home()/.codex``.

    Missing or newer Codex schemas result in empty/unavailable fields rather
    than failures. Local rate-limit values are observation metadata only; the
    dashboard must use the wham API for current usage.
    """
    base_dir = codex_dir or CODEX_DIR
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = _read_threads(base_dir / "state_5.sqlite")

    timestamps = [_as_datetime(row.get("updated_at") or row.get("created_at")) for row in rows]
    valid_timestamps = [timestamp for timestamp in timestamps if timestamp]
    total_tokens = 0
    has_tokens = False
    models: Counter[str] = Counter()
    for row in rows:
        token_value = row.get("tokens_used")
        if isinstance(token_value, (int, float)) and token_value >= 0:
            total_tokens += int(token_value)
            has_tokens = True
        model = row.get("model")
        if isinstance(model, str) and model.strip():
            models[model.strip()] += 1

    return LocalCodexSnapshot(
        session_count=len(rows),
        sessions_last_24h=sum(timestamp >= reference_time - timedelta(hours=24) for timestamp in valid_timestamps),
        sessions_last_7d=sum(timestamp >= reference_time - timedelta(days=7) for timestamp in valid_timestamps),
        tokens_used=total_tokens if has_tokens else None,
        model_breakdown=dict(models.most_common()),
        last_updated_at=max(valid_timestamps).isoformat() if valid_timestamps else None,
    )


def _snapshot_path(path: Optional[Path] = None) -> Path:
    return path or SNAPSHOT_FILE


def record_usage_snapshot(
    usage: Optional[dict], local: LocalCodexSnapshot,
    path: Optional[Path] = None, now: Optional[datetime] = None,
) -> None:
    """Append a privacy-safe aggregate snapshot and retain the latest 500 entries."""
    if not usage:
        return
    snapshot = {
        "timestamp": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "five_hour": usage.get("five_hour"),
        "seven_day": usage.get("seven_day"),
        "tokens_used": local.tokens_used,
        "session_count": local.session_count,
    }
    target = _snapshot_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _snapshot_lock(target):
        entries = read_usage_snapshots(target)
        entries.append(UsageSnapshot(
            timestamp=snapshot["timestamp"],
            five_hour=snapshot["five_hour"],
            seven_day=snapshot["seven_day"],
            tokens_used=snapshot["tokens_used"],
            session_count=snapshot["session_count"],
        ))
        entries = entries[-500:]
        temporary = target.with_name(target.name + ".{}.tmp".format(os.getpid()))
        with temporary.open("w", encoding="utf-8") as snapshot_file:
            for entry in entries:
                snapshot_file.write(json.dumps(entry.__dict__, separators=(",", ":")) + "\n")
        os.replace(temporary, target)


@contextmanager
def _snapshot_lock(target: Path):
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0)
        lock_file.write(b"0")
        lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def read_usage_snapshots(path: Optional[Path] = None) -> list[UsageSnapshot]:
    target = _snapshot_path(path)
    if not target.is_file():
        return []
    entries: list[UsageSnapshot] = []
    try:
        with target.open("r", encoding="utf-8") as snapshot_file:
            for line in snapshot_file:
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        continue
                    entries.append(UsageSnapshot(
                        timestamp=str(value.get("timestamp", "")),
                        five_hour=float(value["five_hour"]) if value.get("five_hour") is not None else None,
                        seven_day=float(value["seven_day"]) if value.get("seven_day") is not None else None,
                        tokens_used=int(value["tokens_used"]) if value.get("tokens_used") is not None else None,
                        session_count=int(value.get("session_count", 0)),
                    ))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    return entries[-500:]
