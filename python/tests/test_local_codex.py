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
            # Create a rollout file under the codex dir
            rollout_path = codex_dir / "rollout.jsonl"
            rollout_path.write_text(json.dumps({
                "type": "event_msg",
                "payload": {"rate_limits": {"primary": {"used_percent": 40}}, "plan_type": "plus"},
            }) + "\n", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            # Use relative path under codex_dir for first row, NULL for second
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:30:00Z", 125, "gpt-5", "rollout.jsonl"),
                    ("2026-08-24T10:00:00Z", "2026-08-24T10:00:00Z", 75, "gpt-5", None),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity includes both rows (valid model, tokens, timestamp)
        self.assertEqual(snapshot.session_count, 2)
        self.assertEqual(snapshot.sessions_last_24h, 1)
        self.assertEqual(snapshot.sessions_last_7d, 1)
        self.assertEqual(snapshot.tokens_used, 200)
        # Model aggregates only include row with valid rollout_path
        self.assertEqual(snapshot.model_breakdown, {"gpt-5": 1})
        self.assertEqual(snapshot.model_tokens_used, {"gpt-5": 125})
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

    def test_model_ordering_by_latest_updated_at_descending(self):
        """Models should be ordered by latest updated_at descending with deterministic tie-break."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files for each model
            for name in ("rollout-a.jsonl", "rollout-b.jsonl", "rollout-c.jsonl"):
                (codex_dir / name).write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            # Model A: latest update 2026-09-01T12:00:00Z
            # Model B: latest update 2026-09-01T14:00:00Z (should come first)
            # Model C: latest update 2026-09-01T10:00:00Z (should come last)
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z", 100, "model-a", "rollout-a.jsonl"),
                    ("2026-09-01T13:00:00Z", "2026-09-01T14:00:00Z", 200, "model-b", "rollout-b.jsonl"),
                    ("2026-09-01T09:00:00Z", "2026-09-01T10:00:00Z", 50, "model-c", "rollout-c.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 15, tzinfo=timezone.utc))

        # model_b (14:00) should come first, then model_a (12:00), then model_c (10:00)
        model_order = list(snapshot.model_breakdown.keys())
        self.assertEqual(model_order, ["model-b", "model-a", "model-c"])

    def test_model_ordering_deterministic_tie_break_on_model_name(self):
        """When updated_at is equal, order deterministically by model name."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files for each model
            for name in ("rollout-zeta.jsonl", "rollout-alpha.jsonl"):
                (codex_dir / name).write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            # Both models have same latest updated_at, should order by model name
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z", 100, "zeta-model", "rollout-zeta.jsonl"),
                    ("2026-09-01T11:00:00Z", "2026-09-01T12:00:00Z", 200, "alpha-model", "rollout-alpha.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 15, tzinfo=timezone.utc))

        # alpha-model should come before zeta-model (alphabetical tie-break)
        model_order = list(snapshot.model_breakdown.keys())
        self.assertEqual(model_order, ["alpha-model", "zeta-model"])

    def test_per_model_last_updated_at_tracking(self):
        """Each model should track its own latest updated_at for 'last used' display."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files for each model
            for name in ("rollout-x.jsonl", "rollout-y.jsonl"):
                (codex_dir / name).write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-x", "rollout-x.jsonl"),
                    ("2026-09-01T12:00:00Z", "2026-09-01T14:00:00Z", 200, "model-y", "rollout-y.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 15, tzinfo=timezone.utc))

        # Check that model_last_updated_at exists and has correct values
        self.assertTrue(hasattr(snapshot, "model_last_updated_at"))
        self.assertEqual(snapshot.model_last_updated_at["model-x"], "2026-09-01T11:00:00+00:00")
        self.assertEqual(snapshot.model_last_updated_at["model-y"], "2026-09-01T14:00:00+00:00")

    def test_aggregate_tokens_per_model(self):
        """Each model should track aggregate tokens used."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files for each model
            for name in ("rollout-x.jsonl", "rollout-y.jsonl"):
                (codex_dir / name).write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-x", "rollout-x.jsonl"),
                    ("2026-09-01T12:00:00Z", "2026-09-01T14:00:00Z", 200, "model-x", "rollout-x.jsonl"),
                    ("2026-09-01T13:00:00Z", "2026-09-01T15:00:00Z", 300, "model-y", "rollout-y.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 16, tzinfo=timezone.utc))

        # Check that model_tokens_used exists and has correct aggregate values
        self.assertTrue(hasattr(snapshot, "model_tokens_used"))
        self.assertEqual(snapshot.model_tokens_used["model-x"], 300)
        self.assertEqual(snapshot.model_tokens_used["model-y"], 300)

    def test_malformed_metadata_excluded(self):
        """Rows with missing/invalid model, tokens_used, or timestamps should be excluded."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout file for the valid row
            (codex_dir / "rollout.jsonl").write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "valid-model", "rollout.jsonl"),
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", -50, "negative-tokens", "rollout.jsonl"),  # negative tokens
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "", "rollout.jsonl"),  # empty model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, None, "rollout.jsonl"),  # null model
                    ("invalid-date", "invalid-date", 100, "bad-timestamps", "rollout.jsonl"),  # invalid timestamps
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", "not-a-number", "bad-tokens", "rollout.jsonl"),  # non-numeric tokens
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Only the valid row should be counted in global activity
        self.assertEqual(snapshot.session_count, 1)
        self.assertEqual(snapshot.tokens_used, 100)
        # Model aggregates only include row with valid rollout_path
        self.assertEqual(snapshot.model_breakdown, {"valid-model": 1})

    def test_rollout_path_scoping_valid_relative_path(self):
        """Valid relative rollout_path under codex_dir is accepted."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            rollout_path = codex_dir / "rollout.jsonl"
            rollout_path.write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", "rollout.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        self.assertEqual(snapshot.session_count, 1)
        self.assertEqual(snapshot.model_breakdown, {"model-a": 1})

    def test_rollout_path_scoping_valid_absolute_path(self):
        """Valid absolute rollout_path under codex_dir is accepted."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            rollout_path = codex_dir / "rollout.jsonl"
            rollout_path.write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", str(rollout_path)),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        self.assertEqual(snapshot.session_count, 1)
        self.assertEqual(snapshot.model_breakdown, {"model-a": 1})

    def test_rollout_path_scoping_outside_path_excluded(self):
        """Rollout path outside codex_dir is excluded from model aggregates.
        Global activity still includes the row (valid model, tokens, timestamp)."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create a separate temporary directory for the outside path
            with tempfile.TemporaryDirectory() as outside_temp:
                outside_dir = Path(outside_temp)
                outside_rollout = outside_dir / "rollout.jsonl"
                outside_rollout.write_text("{}", encoding="utf-8")
                connection = sqlite3.connect(codex_dir / "state_5.sqlite")
                connection.execute(
                    "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
                )
                connection.executemany(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                    [
                        ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", str(outside_rollout)),
                    ],
                )
                connection.commit()
                connection.close()

                snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

            # Global activity includes the row (valid model, tokens, timestamp)
            self.assertEqual(snapshot.session_count, 1)
            self.assertEqual(snapshot.tokens_used, 100)
            # Model aggregates exclude row because rollout_path is outside codex_dir
            self.assertEqual(snapshot.model_breakdown, {})
            self.assertEqual(snapshot.model_tokens_used, {})

    def test_rollout_path_scoping_nonexistent_file_excluded(self):
        """Rollout path that doesn't exist as a file is excluded from model aggregates.
        Global activity still includes the row (valid model, tokens, timestamp)."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", "nonexistent.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity includes the row (valid model, tokens, timestamp)
        self.assertEqual(snapshot.session_count, 1)
        self.assertEqual(snapshot.tokens_used, 100)
        # Model aggregates exclude row because rollout_path doesn't exist
        self.assertEqual(snapshot.model_breakdown, {})
        self.assertEqual(snapshot.model_tokens_used, {})

    def test_missing_updated_at_excluded_from_model_aggregation(self):
        """Row with missing/invalid updated_at is excluded from model aggregation.
        Global activity uses created_at fallback; model aggregates require updated_at."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files for each model
            for name in ("rollout-a.jsonl", "rollout-b.jsonl", "rollout-c.jsonl"):
                (codex_dir / name).write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", "rollout-a.jsonl"),  # valid updated_at
                    ("2026-09-01T10:00:00Z", None, 200, "model-b", "rollout-b.jsonl"),  # missing updated_at, has created_at
                    ("2026-09-01T10:00:00Z", "invalid", 300, "model-c", "rollout-c.jsonl"),  # invalid updated_at, has created_at
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity includes all 3 rows (created_at fallback works)
        self.assertEqual(snapshot.session_count, 3)
        self.assertEqual(snapshot.tokens_used, 600)
        # Model aggregates only include model-a (has valid updated_at AND valid rollout_path)
        self.assertEqual(snapshot.model_breakdown, {"model-a": 1})
        self.assertEqual(snapshot.model_last_updated_at, {"model-a": "2026-09-01T11:00:00+00:00"})
        self.assertEqual(snapshot.model_tokens_used, {"model-a": 100})

    def test_null_rollout_path_excluded_from_model_breakdown(self):
        """NULL rollout_path never populates model_breakdown.
        Global activity still includes the row."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout file for model-b
            (codex_dir / "rollout.jsonl").write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", None),
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 200, "model-b", "rollout.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity includes both rows
        self.assertEqual(snapshot.session_count, 2)
        self.assertEqual(snapshot.tokens_used, 300)
        # Model aggregates only include model-b (has valid rollout_path)
        self.assertEqual(snapshot.model_breakdown, {"model-b": 1})
        self.assertEqual(snapshot.model_tokens_used, {"model-b": 200})

    def test_schema_without_rollout_path_column(self):
        """Schema without rollout_path column produces empty model aggregates.
        Global activity still works with available columns."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            # Table without rollout_path column
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?)",
                [
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a"),
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 200, "model-b"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity includes both rows
        self.assertEqual(snapshot.session_count, 2)
        self.assertEqual(snapshot.tokens_used, 300)
        # Model aggregates are empty (no rollout_path column)
        self.assertEqual(snapshot.model_breakdown, {})
        self.assertEqual(snapshot.model_tokens_used, {})

    def test_mixed_fixture_global_totals_vs_model_aggregates(self):
        """Mixed fixture: global totals include safe rows; model aggregates only strictly eligible rows."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_dir = Path(temporary_directory)
            # Create rollout files
            (codex_dir / "rollout-valid.jsonl").write_text("{}", encoding="utf-8")
            (codex_dir / "rollout-valid2.jsonl").write_text("{}", encoding="utf-8")
            connection = sqlite3.connect(codex_dir / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads (created_at TEXT, updated_at TEXT, tokens_used INTEGER, model TEXT, rollout_path TEXT)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                [
                    # Row 1: fully valid - counts in both global and model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 100, "model-a", "rollout-valid.jsonl"),
                    # Row 2: valid for global, invalid rollout_path for model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 200, "model-b", "nonexistent.jsonl"),
                    # Row 3: valid for global, NULL rollout_path for model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 300, "model-c", None),
                    # Row 4: valid for global, outside rollout_path for model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 400, "model-d", "/outside/path.jsonl"),
                    # Row 5: fully valid - counts in both global and model
                    ("2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 500, "model-e", "rollout-valid2.jsonl"),
                ],
            )
            connection.commit()
            connection.close()

            snapshot = read_local_codex_snapshot(codex_dir, datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

        # Global activity: all 5 rows valid (model, tokens, timestamp)
        self.assertEqual(snapshot.session_count, 5)
        self.assertEqual(snapshot.tokens_used, 1500)
        # Model aggregates: only rows 1 and 5 (valid rollout_path)
        self.assertEqual(snapshot.model_breakdown, {"model-a": 1, "model-e": 1})
        self.assertEqual(snapshot.model_tokens_used, {"model-a": 100, "model-e": 500})
        # Model ordering by updated_at (all same) then by name
        self.assertEqual(list(snapshot.model_breakdown.keys()), ["model-a", "model-e"])
