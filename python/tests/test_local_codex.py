import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_codex import (
    LocalCodexSnapshot,
    parse_rate_limits_event,
    read_local_codex_snapshot,
    read_usage_snapshots,
    record_usage_snapshot,
)


class LocalCodexTests(unittest.TestCase):
    def test_parse_rate_limits_event_reads_only_limit_metadata(self):
        event = {
            "type": "event_msg",
            "payload": {
                "rate_limits": {
                    "primary": {"used_percent": 22.5, "resets_at": "2026-09-01T12:00:00Z", "window_minutes": 300},
                    "secondary": {"used_percent": 11, "window_minutes": 10080},
                },
                "plan_type": "pro",
                "credits": 2,
            },
        }

        primary, secondary, plan, credits = parse_rate_limits_event(event)

        self.assertEqual(primary.used_percent, 22.5)
        self.assertEqual(primary.window_minutes, 300)
        self.assertEqual(secondary.used_percent, 11)
        self.assertEqual(plan, "pro")
        self.assertEqual(credits, 2)

    def test_snapshot_aggregates_thread_metadata_without_reading_rollouts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            rollout_path = codex_dir / "rollout.jsonl"
            rollout_path.write_text(json.dumps({
                "type": "event_msg",
                "payload": {"rate_limits": {"primary": {"used_percent": 40}}, "plan_type": "plus"},
            }) + "\n", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:30:00Z", 125, "gpt-5", str(rollout_path)),
                    ("2026-08-24T10:00:00Z", "2026-08-24T10:00:00Z", 75, "gpt-5", None),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        self.assertEqual(snapshot.session_count, 2)
        self.assertEqual(snapshot.sessions_last_24h, 1)
        self.assertEqual(snapshot.sessions_last_7d, 1)
        self.assertEqual(snapshot.tokens_used, 200)
        self.assertEqual(snapshot.model_breakdown, {"gpt-5": 2})
        self.assertIsNone(snapshot.observed_primary)
        self.assertIsNone(snapshot.observed_plan_type)

    def test_missing_database_produces_unavailable_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = read_local_codex_snapshot(Path(temporary_directory))

        self.assertEqual(snapshot.session_count, 0)
        self.assertIsNone(snapshot.tokens_used)
        self.assertEqual(snapshot.model_breakdown, {})

    def test_usage_snapshots_store_aggregates_without_content(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "snapshots.jsonl"
            record_usage_snapshot(
                {"five_hour": 12, "seven_day": 4},
                LocalCodexSnapshot(tokens_used=900, session_count=3),
                path,
                datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
            snapshots = read_usage_snapshots(path)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].five_hour, 12)
        self.assertEqual(snapshots[0].tokens_used, 900)
