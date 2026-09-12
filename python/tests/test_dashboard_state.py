import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, QSize, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel

import config
from config import AppConfig
from dashboard import DashboardTheme, DashboardWindow, build_dashboard_state, calculate_popup_position, parse_dashboard_anchor
from local_codex import LocalCodexSnapshot


class DashboardStateTests(unittest.TestCase):
    def test_missing_api_usage_is_explicitly_unavailable(self):
        state = build_dashboard_state(None, LocalCodexSnapshot())

        self.assertEqual(state.five_hour, "Not available")
        self.assertEqual(state.seven_day, "Not available")
        self.assertEqual(state.plan, "Not available")
        self.assertEqual(state.credits, "Not available")
        self.assertEqual(state.credit_expiry, "Not available")
        self.assertEqual(state.tokens, "Not available")

    def test_api_limits_and_local_metadata_render_without_estimates(self):
        local = LocalCodexSnapshot(
            session_count=4,
            sessions_last_24h=2,
            sessions_last_7d=3,
            tokens_used=1200,
            model_breakdown={"gpt-5": 4},
            model_last_updated_at={"gpt-5": "2026-09-01T12:00:00+00:00"},
            model_tokens_used={"gpt-5": 1200},
            last_updated_at="2026-09-01T12:00:00+00:00",
        )
        state = build_dashboard_state(
            {
                "five_hour": 25,
                "five_hour_reset": "2026-09-01T14:30:00Z",
                "seven_day": 70.2,
                "seven_day_reset": "2026-09-04T12:00:00Z",
                "plan_type": "pro",
                "reset_credits": 3,
                "reset_credits_expires_at": "2026-09-01T13:00:00Z",
            },
            local,
            datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(state.five_hour, "25%")
        self.assertEqual(state.five_hour_reset, "2h 30m")
        self.assertEqual(state.seven_day, "70%")
        self.assertEqual(state.credit_expiry, "1h 0m")
        self.assertEqual(state.tokens, "1,200")
        # Model rows now show aggregate tokens and last used relative time
        self.assertEqual(state.model_rows, (("gpt-5", "1,200 tokens • last used just now"),))

    def test_model_rows_show_aggregate_tokens_and_last_used_relative_time(self):
        """Model rows display aggregate tokens and truthful 'last used <relative time>'."""
        local = LocalCodexSnapshot(
            session_count=5,
            sessions_last_24h=3,
            sessions_last_7d=5,
            tokens_used=2500,
            model_breakdown={"model-b": 3, "model-a": 2},
            model_last_updated_at={
                "model-b": "2026-09-01T14:00:00+00:00",
                "model-a": "2026-09-01T10:00:00+00:00",
            },
            model_tokens_used={"model-b": 1500, "model-a": 1000},
            last_updated_at="2026-09-01T14:00:00+00:00",
        )
        state = build_dashboard_state(
            None,
            local,
            datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc),  # 30 min after model-b, 4h30m after model-a
        )

        # Models ordered by latest updated_at desc (model-b first, then model-a)
        self.assertEqual(len(state.model_rows), 2)
        self.assertEqual(state.model_rows[0][0], "model-b")
        self.assertEqual(state.model_rows[1][0], "model-a")
        # model-b: 1,500 tokens, last used 30 min ago
        self.assertIn("1,500 tokens", state.model_rows[0][1])
        self.assertIn("last used 30m", state.model_rows[0][1])
        # model-a: 1,000 tokens, last used 4h 30m ago
        self.assertIn("1,000 tokens", state.model_rows[1][1])
        self.assertIn("last used 4h 30m", state.model_rows[1][1])

    def test_model_rows_include_exact_72_hour_boundary_only(self):
        now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
        local = LocalCodexSnapshot(
            session_count=9,
            sessions_last_24h=2,
            sessions_last_7d=7,
            tokens_used=9000,
            model_breakdown={
                "at-boundary": 3,
                "recent": 4,
                "expired": 1,
                "malformed": 1,
            },
            model_last_updated_at={
                "at-boundary": (now - timedelta(hours=72)).isoformat(),
                "recent": (now - timedelta(hours=1)).isoformat(),
                "expired": (now - timedelta(hours=72, seconds=1)).isoformat(),
                "malformed": "not-a-timestamp",
            },
            model_tokens_used={"at-boundary": 3000, "recent": 4000, "expired": 1000, "malformed": 1000},
        )

        state = build_dashboard_state(None, local, now)

        self.assertEqual([model for model, _ in state.model_rows], ["at-boundary", "recent"])
        self.assertEqual(state.tokens, "9,000")
        self.assertEqual(state.total_sessions, "9")

    def test_model_rows_fallback_when_no_model_data(self):
        """When no model data available, show fallback row."""
        local = LocalCodexSnapshot()
        state = build_dashboard_state(None, local)

        self.assertEqual(state.model_rows, (("Model breakdown", "Not available"),))

    def test_popup_position_clamps_an_oversized_panel_inside_available_geometry(self):
        position = calculate_popup_position(
            QPoint(499, 349), QSize(420, 300), QRect(100, 50, 400, 300),
        )

        self.assertEqual(position, QPoint(108, 58))

    def test_dashboard_anchor_requires_two_valid_coordinates(self):
        self.assertEqual(
            parse_dashboard_anchor(["--dashboard", "--dashboard-x=140", "--dashboard-y=860"]),
            QPoint(140, 860),
        )
        self.assertIsNone(parse_dashboard_anchor(["--dashboard-x=not-a-number", "--dashboard-y=860"]))
        self.assertIsNone(parse_dashboard_anchor(["--dashboard-x=140"]))


class DashboardWidgetTests(unittest.TestCase):
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

    def test_theme_toggle_rethemes_widget_and_preserves_newer_settings(self):
        light = DashboardTheme.LIGHT_PALETTE
        self.assertIn("background: {};".format(light["window"]), self.window.styleSheet())
        self.assertEqual(self.window._theme_button.text(), "☾")
        self.assertEqual(self.window._theme_button.toolTip(), "Switch to dark mode")
        self.assertEqual(self.window._theme_button.accessibleName(), "Switch to dark mode")

        settings_config = config.load_config()
        settings_config.refresh_interval_seconds = 120
        settings_config.cache_ttl_seconds = 90
        config.save_config(settings_config)
        self.window._theme_button.click()

        dark = DashboardTheme.DARK_PALETTE
        self.assertIn("background: {};".format(dark["window"]), self.window.styleSheet())
        self.assertEqual(self.window._theme_button.text(), "☀")
        self.assertEqual(self.window._theme_button.toolTip(), "Switch to light mode")
        self.assertEqual(self.window._theme_button.accessibleName(), "Switch to light mode")
        self.assertTrue(self.window._config.dark_mode)
        self.assertEqual(self.window._config.refresh_interval_seconds, 120)
        self.assertEqual(config.load_config(), self.window._config)

    def test_model_row_elides_long_name_and_keeps_detail_visible(self):
        now = datetime.now(timezone.utc)
        long_model_name = "experimental/" + "variant-" * 80
        local = LocalCodexSnapshot(
            model_breakdown={long_model_name: 1},
            model_last_updated_at={long_model_name: now.isoformat()},
            model_tokens_used={long_model_name: 1200},
        )

        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(None, local)
        self.window.show()
        self.app.processEvents()

        row = self.window.findChild(QFrame, "modelRow")
        self.assertIsNotNone(row)
        model_label = row.findChild(QLabel, "modelName")
        detail_label = row.findChild(QLabel, "modelDetail")
        self.assertFalse(model_label.wordWrap())
        self.assertNotEqual(model_label.text(), long_model_name)
        self.assertEqual(model_label.text(), model_label.fontMetrics().elidedText(
            long_model_name,
            Qt.TextElideMode.ElideRight,
            model_label.contentsRect().width(),
        ))
        self.assertTrue(detail_label.wordWrap())
        self.assertIn("1,200 tokens", detail_label.text())
