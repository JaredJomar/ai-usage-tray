import os
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel

import config
from config import AppConfig
from dashboard import (
    DashboardRefreshWorker,
    DashboardWindow,
    _format_epoch_age,
    _refresh_status_text,
    build_dashboard_state,
)
from local_codex import LocalCodexSnapshot
from providers.codex import (
    FetchResult,
    STATUS_API_ERROR,
    STATUS_AUTH_REQUIRED,
    STATUS_FRESH,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
)


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def test_format_epoch_age_tolerates_non_finite_timestamps():
    assert _format_epoch_age(float("nan")) == "unknown"
    assert _format_epoch_age(float("inf")) == "unknown"
    assert _format_epoch_age(float("-inf")) == "unknown"


def test_fresh_status_reports_last_success_age():
    text, detail = _refresh_status_text(
        FetchResult(STATUS_FRESH, {}, NOW.timestamp() - 720, "Updated from API"), {}, NOW,
    )

    assert text == "Current limits updated 12m ago"
    assert detail == "Updated from API"


def test_stale_status_shows_failure_and_prior_data_age():
    text, detail = _refresh_status_text(
        FetchResult(STATUS_STALE, {"five_hour": 1}, NOW.timestamp() - 300, "API returned HTTP 503"),
        {"five_hour": 1}, NOW,
    )

    assert text == "Refresh failed · showing limits from 5m ago"
    assert "503" in detail


def test_auth_required_status_with_data_keeps_limits_and_age():
    text, _ = _refresh_status_text(
        FetchResult(STATUS_AUTH_REQUIRED, {"five_hour": 1}, NOW.timestamp() - 120, "Session expired"),
        {"five_hour": 1}, NOW,
    )

    assert text == "Login required · showing limits from 2m ago"


def test_auth_required_status_without_data_prompts_login():
    text, detail = _refresh_status_text(
        FetchResult(STATUS_AUTH_REQUIRED, None, None, "Not logged in"), None, NOW,
    )

    assert text == "Login required to refresh limits"
    assert detail == "Not logged in"


def test_api_error_status_surfaces_detail():
    text, _ = _refresh_status_text(
        FetchResult(STATUS_API_ERROR, None, None, "API returned HTTP 503"), None, NOW,
    )

    assert text == "Refresh failed: API returned HTTP 503"


def test_unavailable_status_keeps_local_metadata_message():
    text, _ = _refresh_status_text(
        FetchResult(STATUS_UNAVAILABLE, None, None, "Unexpected API response"), None, NOW,
    )

    assert text == "Current limits unavailable; local metadata shown"


def test_legacy_status_without_result_is_unchanged():
    assert _refresh_status_text(None, {"five_hour": 1})[0] == "Current limits loaded"
    assert _refresh_status_text(None, None)[0] == (
        "Current limits unavailable; local metadata shown"
    )


def test_build_dashboard_state_carries_refresh_status():
    state = build_dashboard_state(
        None, LocalCodexSnapshot(), NOW, (),
        FetchResult(STATUS_STALE, None, NOW.timestamp() - 60, "API returned HTTP 500"),
    )

    assert state.refresh_status == "Refresh failed · showing limits from 1m ago"
    assert state.refresh_detail == "API returned HTTP 500"
    assert state.five_hour == "Not available"


class DashboardRefreshStatusWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config_path = Path(self.temp_dir.name) / "config.json"
        self.config_dir_patch = patch.object(config, "CONFIG_DIR", config_path.parent)
        self.config_file_patch = patch.object(config, "CONFIG_FILE", config_path)
        self.config_dir_patch.start()
        self.config_file_patch.start()
        self.addCleanup(self.config_file_patch.stop)
        self.addCleanup(self.config_dir_patch.stop)
        self.timer_patch = patch.object(QTimer, "singleShot")
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        config.save_config(AppConfig(refresh_interval_seconds=60, cache_ttl_seconds=30))
        self.window = DashboardWindow(config.load_config())
        self.addCleanup(self.window.close)

    def test_render_stale_result_updates_status_and_keeps_metrics(self):
        now = datetime.now(timezone.utc)
        usage = {
            "five_hour": 25,
            "five_hour_reset": (now + timedelta(hours=2)).isoformat(),
            "seven_day": 70.0,
            "seven_day_reset": (now + timedelta(days=3)).isoformat(),
            "plan_type": "pro",
            "reset_credits": 3,
        }
        result = FetchResult(STATUS_STALE, usage, time.time() - 600, "API returned HTTP 503")

        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(usage, LocalCodexSnapshot(), result)

        status = self.window.findChild(QLabel, "subtitle")
        self.assertIsNotNone(status)
        self.assertIn("Refresh failed", status.text())
        self.assertIn("showing limits from", status.text())
        self.assertIn("503", status.toolTip())
        self.assertEqual(self.window.findChild(QLabel, "fiveRemainingValue").text(), "75%")
        self.assertEqual(self.window.findChild(QLabel, "creditsValue").text(), "3")

    def test_render_without_result_keeps_legacy_status(self):
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(None, LocalCodexSnapshot())

        status = self.window.findChild(QLabel, "subtitle")
        self.assertEqual(status.text(), "Current limits unavailable; local metadata shown")

    def test_render_with_usage_and_no_result_keeps_loaded_status(self):
        usage = {"five_hour": 10.0, "seven_day": 20.0}

        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(usage, LocalCodexSnapshot())

        status = self.window.findChild(QLabel, "subtitle")
        self.assertEqual(status.text(), "Current limits loaded")

    def test_render_auth_required_without_data_prompts_login(self):
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(
                None, LocalCodexSnapshot(),
                FetchResult(STATUS_AUTH_REQUIRED, None, None, "Not logged in"),
            )

        status = self.window.findChild(QLabel, "subtitle")
        self.assertEqual(status.text(), "Login required to refresh limits")
        self.assertEqual(self.window.findChild(QLabel, "fiveRemainingValue").text(), "Not available")

    def test_render_retains_prior_quota_when_refresh_fails(self):
        now = datetime.now(timezone.utc)
        usage = {
            "five_hour": 25,
            "five_hour_reset": (now + timedelta(hours=2)).isoformat(),
            "seven_day": 70.0,
            "seven_day_reset": (now + timedelta(days=3)).isoformat(),
            "plan_type": "pro",
            "reset_credits": 3,
        }
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(
                usage, LocalCodexSnapshot(),
                FetchResult(STATUS_FRESH, usage, time.time() - 60),
            )
            self.window._render(
                None, LocalCodexSnapshot(),
                FetchResult(STATUS_API_ERROR, None, None, "API returned HTTP 503"),
            )

        status = self.window.findChild(QLabel, "subtitle")
        self.assertIn("Refresh failed", status.text())
        self.assertIn("showing limits from", status.text())
        self.assertEqual(self.window.findChild(QLabel, "fiveRemainingValue").text(), "75%")
        self.assertEqual(self.window.findChild(QLabel, "creditsValue").text(), "3")

    def test_render_retains_prior_quota_when_login_required(self):
        now = datetime.now(timezone.utc)
        usage = {
            "five_hour": 25,
            "five_hour_reset": (now + timedelta(hours=2)).isoformat(),
            "seven_day": 70.0,
            "seven_day_reset": (now + timedelta(days=3)).isoformat(),
        }
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(
                usage, LocalCodexSnapshot(),
                FetchResult(STATUS_FRESH, usage, time.time() - 120),
            )
            self.window._render(
                None, LocalCodexSnapshot(),
                FetchResult(STATUS_AUTH_REQUIRED, None, None, "Session expired"),
            )

        status = self.window.findChild(QLabel, "subtitle")
        self.assertIn("Login required", status.text())
        self.assertIn("showing limits from", status.text())
        self.assertEqual(self.window.findChild(QLabel, "fiveRemainingValue").text(), "75%")

    def test_status_timer_recomputes_age_from_stored_result(self):
        now = datetime.now(timezone.utc)
        usage = {
            "five_hour": 25,
            "five_hour_reset": (now + timedelta(hours=2)).isoformat(),
            "seven_day": 70.0,
            "seven_day_reset": (now + timedelta(days=3)).isoformat(),
        }
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(
                usage, LocalCodexSnapshot(),
                FetchResult(STATUS_FRESH, usage, time.time() - 90),
            )

        status = self.window.findChild(QLabel, "subtitle")
        self.assertIn("1m ago", status.text())

        self.window._rendered_result = FetchResult(
            STATUS_FRESH, usage, time.time() - 3900, "Updated from API",
        )
        self.window._update_status_display()

        self.assertIn("1h", status.text())

    def test_status_timer_skips_update_while_refresh_running(self):
        usage = {"five_hour": 25.0, "seven_day": 70.0}
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(
                usage, LocalCodexSnapshot(),
                FetchResult(STATUS_FRESH, usage, time.time() - 60),
            )

        status = self.window.findChild(QLabel, "subtitle")
        before = status.text()

        class _RunningWorker:
            def isRunning(self):
                return True

            def requestInterruption(self):
                pass

            def terminate(self):
                pass

            def wait(self, msecs=None):
                return True

        self.window._worker = _RunningWorker()
        self.window._rendered_result = FetchResult(
            STATUS_FRESH, usage, time.time() - 3600, "Updated from API",
        )
        self.window._update_status_display()

        self.assertEqual(status.text(), before)

    def test_status_timer_fires_every_30_seconds(self):
        self.assertEqual(self.window._status_timer.interval(), DashboardWindow.STATUS_TICK_MS)
        self.assertTrue(self.window._status_timer.isActive())

    def test_refresh_worker_exception_emits_api_error_result(self):
        worker = DashboardRefreshWorker(AppConfig(codex_enabled=True))
        received = []
        worker.refreshed.connect(lambda usage, local, result: received.append(result))
        with patch("dashboard.read_local_codex_snapshot", return_value=LocalCodexSnapshot()), \
             patch("dashboard.CacheManager"), \
             patch("dashboard.CodexProvider") as provider_cls:
            provider_cls.return_value.fetch_result.side_effect = RuntimeError("boom")
            worker.run()

        self.assertEqual(len(received), 1)
        result = received[0]
        self.assertEqual(result.status, STATUS_API_ERROR)
        self.assertIsNone(result.usage)
        self.assertEqual(result.detail, "Unexpected error")
