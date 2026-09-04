import unittest
from datetime import datetime, timezone

from PyQt6.QtCore import QPoint, QRect, QSize

from dashboard import build_dashboard_state, calculate_popup_position, parse_dashboard_anchor
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
        self.assertEqual(state.model_rows, (("gpt-5", "4 sessions"),))

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
