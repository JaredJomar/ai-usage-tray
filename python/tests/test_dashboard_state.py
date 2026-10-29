import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, QSize, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QFrame, QLabel, QMessageBox, QProgressBar, QScrollArea, QToolButton

import config
from config import AppConfig
from dashboard import (
    DashboardResetWorker,
    DashboardTheme,
    DashboardWindow,
    ElidedLabel,
    build_dashboard_state,
    calculate_popup_position,
    parse_dashboard_anchor,
    _filter_history_period,
    _group_history_by_day,
    _remaining_balance,
)
from local_codex import LocalCodexSnapshot, UsageSnapshot
from providers.codex import ResetConsumeResult


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

    def test_remaining_balance_derives_percent_or_reports_unavailable(self):
        self.assertEqual(_remaining_balance("25%"), ("75%", 75))
        self.assertEqual(_remaining_balance("0%"), ("100%", 100))
        self.assertEqual(_remaining_balance("100%"), ("0%", 0))
        self.assertEqual(_remaining_balance("Not available"), ("Not available", 0))

    def test_format_refresh_interval_removed_with_auto_reload_card(self):
        import dashboard

        self.assertFalse(hasattr(dashboard, "_format_refresh_interval"))

    def test_history_period_filter_keeps_only_windowed_snapshots(self):
        history = (
            UsageSnapshot("2026-09-01T11:00:00+00:00", 10.0, 20.0, 100, 1),
            UsageSnapshot("2026-09-06T12:00:00+00:00", 30.0, 40.0, 200, 2),
            UsageSnapshot("2026-09-08T12:00:00+00:00", 50.0, 60.0, 300, 3),
        )
        now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)

        seven_days = _filter_history_period(history, 7, now)
        thirty_days = _filter_history_period(history, 30, now)
        unbounded = _filter_history_period(history, None, now)

        self.assertEqual([s.timestamp for s in seven_days], [history[1].timestamp, history[2].timestamp])
        self.assertEqual(len(thirty_days), 3)
        self.assertEqual(unbounded, history)

    def test_group_history_by_day_averages_real_snapshots(self):
        history = (
            UsageSnapshot("2026-09-07T12:00:00+00:00", 20.0, None, None, 1),
            UsageSnapshot("2026-09-07T12:01:00+00:00", 40.0, None, None, 1),
            UsageSnapshot("2026-09-08T12:00:00+00:00", None, None, None, 1),
        )

        grouped = _group_history_by_day(history)

        self.assertEqual(len(grouped), 1)
        day_label, average, count = grouped[0]
        self.assertEqual(average, 30.0)
        self.assertEqual(count, 2)
        self.assertIn("Sep", day_label)


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
        self.assertIn("background: transparent;", self.window.styleSheet())
        self.assertTrue(self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertEqual(self.window._theme_button.text(), "☾")
        self.assertEqual(self.window._theme_button.toolTip(), "Switch to dark mode")
        self.assertEqual(self.window._theme_button.accessibleName(), "Switch to dark mode")

        settings_config = config.load_config()
        settings_config.refresh_interval_seconds = 120
        settings_config.cache_ttl_seconds = 90
        config.save_config(settings_config)
        self.window._theme_button.click()

        dark = DashboardTheme.DARK_PALETTE
        self.assertIn("background: transparent;", self.window.styleSheet())
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

    def test_shell_width_is_fixed_near_500px_and_scrolls(self):
        self.assertEqual(self.window.width(), 500)
        scroll = self.window.findChild(QScrollArea, "dashboardScroll")
        self.assertIsNotNone(scroll)
        self.assertTrue(scroll.widgetResizable())

    def test_analytics_tabs_period_controls_and_sections_exist(self):
        for name in ("usageTab", "period7D", "period1M", "periodCustom", "groupByDay"):
            self.assertIsNotNone(self.window.findChild(QToolButton, name), name)
        self.assertFalse(self.window.findChild(QToolButton, "periodCustom").isEnabled())
        self.assertEqual(self.window.findChild(QToolButton, "periodCustom").toolTip(), "Custom range is not available")
        titles = [label.text() for label in self.window.findChildren(QLabel, "sectionTitle")]
        for expected in ("Balance", "Usage breakdown", "Local activity"):
            self.assertIn(expected, titles)
        self.assertNotIn("Usage limit resets", titles)
        self.assertIsNotNone(self.window.findChild(QProgressBar, "fiveRemainingBar"))
        self.assertIsNotNone(self.window.findChild(QProgressBar, "weeklyRemainingBar"))
        self.assertIsNotNone(self.window.findChild(QLabel, "fiveRemainingValue"))
        self.assertIsNotNone(self.window.findChild(QLabel, "weeklyRemainingValue"))
        self.assertIsNotNone(self.window.findChild(QLabel, "creditsValue"))

    def test_analytics_title_wraps_without_truncating(self):
        self.window.show()
        self.app.processEvents()

        title = self.window.findChild(QLabel, "title")
        self.assertEqual(title.text(), "Codex Usage")
        self.assertTrue(title.wordWrap())

    def test_section_toggles_are_clickable_and_history_persists(self):
        self.window.show()
        self.window._settings_button.click()
        self.app.processEvents()

        expected_checks = {
            "balanceSectionCheck": "balance",
            "creditsSectionCheck": "credits",
            "usageBreakdownSectionCheck": "usage_breakdown",
            "localActivitySectionCheck": "local_activity",
            "historySectionCheck": "history",
        }
        for object_name in expected_checks:
            check = self.window.findChild(QCheckBox, object_name)
            self.assertIsNotNone(check, object_name)
            self.assertTrue(check.isChecked())

        history_check = self.window.findChild(QCheckBox, "historySectionCheck")
        history_check.click()

        self.assertFalse(history_check.isChecked())
        self.assertFalse(self.window._history_card.isVisibleTo(self.window))
        self.assertFalse(config.load_config().section_visibility.history)

        history_check.click()
        self.assertTrue(self.window._history_card.isVisibleTo(self.window))
        self.assertTrue(config.load_config().section_visibility.history)

    def test_codex_analytics_toggle_is_removed_from_dashboard_settings(self):
        self.window.show()
        self.app.processEvents()

        title = self.window.findChild(QLabel, "title")
        self.assertIsNotNone(title)
        check = self.window.findChild(QCheckBox, "codexAnalyticsSectionCheck")
        self.assertIsNone(check)
        self.assertEqual(title.text(), "Codex Usage")
        self.assertTrue(title.isVisibleTo(self.window))

    def test_group_by_day_hidden_when_history_disabled_and_restored_when_enabled(self):
        """Regression: Group by Day belongs to History and follows its toggle."""
        group_by_day = self.window.findChild(QToolButton, "groupByDay")
        history_check = self.window.findChild(QCheckBox, "historySectionCheck")
        self.assertIsNotNone(group_by_day)
        self.assertIsNotNone(history_check)
        self.assertTrue(group_by_day.isVisibleTo(self.window))

        history_check.setChecked(False)
        self.assertFalse(self.window._history_card.isVisibleTo(self.window))
        self.assertFalse(group_by_day.isVisibleTo(self.window))

        history_check.setChecked(True)
        self.assertTrue(self.window._history_card.isVisibleTo(self.window))
        self.assertTrue(group_by_day.isVisibleTo(self.window))

    def test_section_switch_track_accepts_mouse_clicks(self):
        self.window.show()
        self.window._settings_button.click()
        self.app.processEvents()

        check = self.window.findChild(QCheckBox, "historySectionCheck")
        self.assertIsNotNone(check)
        track_point = QPoint(check.width() - 8, check.height() // 2)
        before = check.isChecked()

        QTest.mouseClick(check, Qt.MouseButton.LeftButton, pos=track_point)

        self.assertNotEqual(check.isChecked(), before)

    def test_auto_reload_card_and_usage_limit_resets_section_are_removed(self):
        """Regression: Auto reload card and redundant Usage limit resets stay gone."""
        self.assertIsNone(self.window.findChild(QFrame, "infoCard"))
        self.assertIsNone(self.window.findChild(QLabel, "autoReloadValue"))
        self.assertIsNone(self.window.findChild(QLabel, "availableFiveReset"))
        self.assertIsNone(self.window.findChild(QLabel, "availableWeeklyReset"))
        self.assertIsNone(self.window.findChild(QLabel, "availableCreditExpiry"))
        titles = [label.text() for label in self.window.findChildren(QLabel, "sectionTitle")]
        self.assertNotIn("Usage limit resets", titles)
        stylesheet = self.window.styleSheet()
        self.assertNotIn("infoCard", stylesheet)
        self.assertNotIn("autoReloadValue", stylesheet)
        self.assertNotIn("availableFiveReset", stylesheet)
        self.assertIsNotNone(self.window.findChild(QLabel, "historyValue"))
        self.assertIsNotNone(self.window.findChild(QToolButton, "period7D"))

    def test_reset_confirmation_is_centered_on_screen_and_above_dashboard(self):
        self.window._render({"five_hour": 25, "seven_day": 70, "reset_credits": 1}, LocalCodexSnapshot())
        self.window.move(0, 0)
        self.window.show()
        observed = {}
        timer = QTimer()
        timer.setSingleShot(True)

        def inspect_confirmation():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                observed["center"] = dialog.frameGeometry().center()
                observed["topmost"] = bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                observed["parent"] = dialog.parentWidget()
                dialog.done(QMessageBox.StandardButton.No)

        timer.timeout.connect(inspect_confirmation)
        timer.start(50)
        with patch("dashboard.CodexProvider.consume_reset_credit") as consume:
            self.window._confirm_reset()
        consume.assert_not_called()
        self.assertIn("center", observed)
        expected = self.window.screen().availableGeometry().center()
        self.assertLessEqual((observed["center"] - expected).manhattanLength(), 2)
        self.assertTrue(observed["topmost"])
        self.assertIs(observed["parent"], self.window)

    def test_use_reset_button_confirms_before_using_credit_when_weekly_quota_is_unexhausted(self):
        usage = {
            "five_hour": 25,
            "five_hour_reset": "2026-09-01T14:30:00Z",
            "seven_day": 70,
            "seven_day_reset": "2026-09-04T12:00:00Z",
            "reset_credits": 3,
        }

        with patch("dashboard.QMessageBox.exec", return_value=QMessageBox.StandardButton.No) as confirm:
            self.window._render(usage, LocalCodexSnapshot())
            button = self.window.findChild(QToolButton, "useResetButton")
            self.assertIsNotNone(button)
            self.assertTrue(button.isEnabled())
            self.assertEqual(button.text(), "Use reset")
            button.click()

        confirm.assert_called_once()
        self.assertTrue(button.isEnabled())

    def test_confirmed_unexhausted_weekly_window_attempts_consume_and_shows_rejection(self):
        usage = {"five_hour": 25, "seven_day": 70, "reset_credits": 1}
        with patch("dashboard.QMessageBox.exec", return_value=QMessageBox.StandardButton.Yes), \
             patch(
                 "dashboard.CodexProvider.consume_reset_credit",
                 return_value=ResetConsumeResult("api_error", "API returned HTTP 403"),
             ) as consume, \
             patch("dashboard.DashboardResetWorker.start", lambda worker: worker.run()):
            self.window._render(usage, LocalCodexSnapshot())
            self.window._confirm_reset()

        consume.assert_called_once()
        self.assertIn("Reset credit not used: API returned HTTP 403", self.window._status.text())

    def test_reset_button_stays_disabled_while_consume_worker_is_running(self):
        self.window._reset_worker = DashboardResetWorker(self.window._config, "request-1")
        with patch("dashboard.DashboardResetWorker.isRunning", return_value=True), \
             patch("dashboard.QMessageBox.exec") as confirm:
            self.window._render(
                {"five_hour": 25, "seven_day": 70, "reset_credits": 1},
                LocalCodexSnapshot(),
            )
            self.window._confirm_reset()

        self.assertFalse(self.window._use_reset_button.isEnabled())
        confirm.assert_not_called()

    def test_use_reset_button_starts_disabled_and_disables_when_credits_are_zero(self):
        button = self.window.findChild(QToolButton, "useResetButton")
        self.assertIsNotNone(button)
        self.assertFalse(button.isEnabled())

        self.window._render({"reset_credits": 2, "seven_day": 100}, LocalCodexSnapshot())
        self.assertTrue(button.isEnabled())
        self.window._render({"reset_credits": 0}, LocalCodexSnapshot())
        self.assertFalse(button.isEnabled())

    def test_settings_icon_toggles_inline_settings_panel(self):
        settings_button = self.window.findChild(QToolButton, "settingsToggle")
        self.assertIsNotNone(settings_button)
        self.assertTrue(settings_button.isCheckable())
        self.assertEqual(settings_button.toolTip(), "Toggle dashboard settings")
        self.assertEqual(settings_button.accessibleName(), "Toggle dashboard settings")
        self.assertEqual(settings_button.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        panel = self.window.findChild(QFrame, "settingsPanel")
        self.assertIsNotNone(panel)
        self.assertFalse(panel.isVisibleTo(self.window))

        settings_button.click()
        self.assertTrue(panel.isVisibleTo(self.window))

        settings_button.click()
        self.assertFalse(panel.isVisibleTo(self.window))

    def test_section_checkboxes_toggle_visibility_and_persist(self):
        self.window._settings_button.click()
        expected_checks = {
            "balanceSectionCheck": "balance",
            "creditsSectionCheck": "credits",
            "usageBreakdownSectionCheck": "usage_breakdown",
            "localActivitySectionCheck": "local_activity",
        }
        for object_name in expected_checks:
            self.assertIsNotNone(self.window.findChild(QCheckBox, object_name), object_name)

        balance_check = self.window.findChild(QCheckBox, "balanceSectionCheck")
        self.assertTrue(balance_check.isChecked())
        self.assertTrue(self.window._five_balance[0].isVisibleTo(self.window))

        balance_check.setChecked(False)

        self.assertFalse(self.window._five_balance[0].isVisibleTo(self.window))
        self.assertFalse(self.window._weekly_balance[0].isVisibleTo(self.window))
        self.assertTrue(self.window._credits_card.isVisibleTo(self.window))
        visibility = config.load_config().section_visibility
        self.assertFalse(visibility.balance)
        self.assertTrue(visibility.credits)
        self.assertTrue(visibility.usage_breakdown)
        self.assertTrue(visibility.local_activity)

        credits_check = self.window.findChild(QCheckBox, "creditsSectionCheck")
        credits_check.setChecked(False)

        self.assertFalse(self.window._credits_card.isVisibleTo(self.window))
        self.assertFalse(config.load_config().section_visibility.credits)

    def test_hidden_sections_restore_from_saved_config_on_open(self):
        saved = config.load_config()
        saved.section_visibility.balance = False
        saved.section_visibility.local_activity = False
        config.save_config(saved)

        reopened = DashboardWindow(config.load_config())
        self.addCleanup(reopened.close)

        self.assertFalse(reopened._five_balance[0].isVisibleTo(reopened))
        self.assertFalse(reopened._weekly_balance[0].isVisibleTo(reopened))
        self.assertFalse(reopened._last_update.isVisibleTo(reopened))
        self.assertFalse(reopened._observation.isVisibleTo(reopened))
        self.assertTrue(reopened._credits_card.isVisibleTo(reopened))
        balance_check = reopened.findChild(QCheckBox, "balanceSectionCheck")
        self.assertFalse(balance_check.isChecked())
        local_check = reopened.findChild(QCheckBox, "localActivitySectionCheck")
        self.assertFalse(local_check.isChecked())

    def test_render_fills_balance_credits_and_resets_from_api_state(self):
        now = datetime.now(timezone.utc)
        local = LocalCodexSnapshot(
            session_count=4,
            sessions_last_24h=2,
            sessions_last_7d=3,
            tokens_used=1200,
            last_updated_at=now.isoformat(),
        )
        usage = {
            "five_hour": 25,
            "five_hour_reset": (now + timedelta(hours=2, minutes=30, seconds=30)).isoformat(),
            "seven_day": 70.0,
            "seven_day_reset": (now + timedelta(days=3, minutes=30)).isoformat(),
            "plan_type": "pro",
            "reset_credits": 3,
            "reset_credits_expires_at": (now + timedelta(hours=1, seconds=30)).isoformat(),
        }

        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(usage, local)

        five_value = self.window.findChild(QLabel, "fiveRemainingValue")
        weekly_value = self.window.findChild(QLabel, "weeklyRemainingValue")
        five_bar = self.window.findChild(QProgressBar, "fiveRemainingBar")
        weekly_bar = self.window.findChild(QProgressBar, "weeklyRemainingBar")
        credits = self.window.findChild(QLabel, "creditsValue")
        plan = self.window._plan

        self.assertEqual(five_value.text(), "75%")
        self.assertEqual(weekly_value.text(), "30%")
        self.assertEqual(five_bar.value(), 75)
        self.assertEqual(weekly_bar.value(), 30)
        self.assertEqual(credits.text(), "3")
        self.assertEqual(plan.text(), "Plan: pro")
        self.assertIn("Resets: 2h 30m", self.window._five_balance[3].text())
        self.assertIn("Resets: 3d 0h", self.window._weekly_balance[3].text())

    def test_render_keeps_unknown_metrics_clearly_unavailable(self):
        with patch("dashboard.read_usage_snapshots", return_value=[]):
            self.window._render(None, LocalCodexSnapshot())

        self.assertEqual(self.window.findChild(QLabel, "fiveRemainingValue").text(), "Not available")
        self.assertEqual(self.window.findChild(QLabel, "weeklyRemainingValue").text(), "Not available")
        self.assertEqual(self.window.findChild(QProgressBar, "fiveRemainingBar").value(), 0)
        self.assertEqual(self.window.findChild(QProgressBar, "weeklyRemainingBar").value(), 0)
        self.assertEqual(self.window.findChild(QLabel, "creditsValue").text(), "Not available")
        self.assertEqual(self.window._plan.text(), "Plan: Not available")

    def test_history_view_uses_real_snapshots_and_period_controls(self):
        now = datetime.now(timezone.utc)
        history = (
            UsageSnapshot((now - timedelta(days=1)).isoformat(), 40.0, None, None, 1),
            UsageSnapshot(now.isoformat(), 80.0, None, None, 1),
        )

        with patch("dashboard.read_usage_snapshots", return_value=history):
            self.window._render(None, LocalCodexSnapshot())

        trend = self.window.findChild(QLabel, "historyValue")
        self.assertIn("snapshots", trend.text())
        self.assertTrue(any(block in trend.text() for block in "@#*+="))

        self.window.findChild(QToolButton, "period1M").click()
        self.assertIn("snapshots", trend.text())

        self.window.findChild(QToolButton, "groupByDay").click()
        self.assertIn("avg", trend.text())
        self.assertIn("sample", trend.text())

        stale = (UsageSnapshot((now - timedelta(days=20)).isoformat(), 10.0, None, None, 1),)
        with patch("dashboard.read_usage_snapshots", return_value=stale):
            self.window._render(None, LocalCodexSnapshot())
        self.window.findChild(QToolButton, "period7D").click()
        self.assertEqual(trend.text(), "Usage history: no snapshots in selected period")
