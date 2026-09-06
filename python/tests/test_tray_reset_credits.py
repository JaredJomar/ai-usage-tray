from datetime import datetime, timedelta, timezone
import threading

import tray as tray_module
from config import AppConfig
from tray import TrayManager


class NotificationIcon:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.notifications = []

    def notify(self, message, title=None):
        self.notifications.append((message, title))


def _manager_with_credit(expiry):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig()
    manager._codex_data = {
        "five_hour": 95,
        "seven_day": 0,
        "reset_credits": 1,
        "reset_credit_expiries": [{"id": "credit-1", "expires_at": expiry}],
    }
    manager._tray_icon = NotificationIcon()
    manager._credit_alerts_sent = set()
    manager._credit_alert_lock = threading.Lock()
    manager._last_notify_time = datetime.now()
    return manager


def test_credit_alerts_cross_each_threshold_once_without_usage_cooldown_suppression():
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now + timedelta(minutes=50)).isoformat())

    manager._check_notifications()
    manager._check_notifications()
    manager._codex_data["reset_credit_expiries"][0]["expires_at"] = (now + timedelta(minutes=14)).isoformat()
    manager._check_notifications()
    manager._check_notifications()

    assert len(manager._tray_icon.notifications) == 2
    assert "60 minutes" in manager._tray_icon.notifications[0][0]
    assert "15 minutes" in manager._tray_icon.notifications[1][0]


def test_credit_first_seen_inside_fifteen_minutes_only_alerts_at_fifteen():
    manager = _manager_with_credit((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

    manager._check_notifications()

    assert len(manager._tray_icon.notifications) == 1
    assert "15 minutes" in manager._tray_icon.notifications[0][0]


def test_tooltip_includes_reset_count_and_nearest_credit_expiry():
    manager = TrayManager.__new__(TrayManager)
    manager._codex_data = {
        "five_hour": 1,
        "five_hour_reset": "",
        "seven_day": 2,
        "seven_day_reset": "",
        "reset_credits": 3,
        "reset_credit_expiries": [
            {"id": "credit-1", "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()},
        ],
    }

    tooltip = manager._build_tooltip()

    assert "RESETS: 3" in tooltip
    assert "expires" in tooltip


def test_dashboard_launch_retries_if_child_exits_during_startup(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    child = type("Child", (), {"poll": lambda self: 1})()
    launches = []

    monkeypatch.setattr(tray_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(tray_module.subprocess, "Popen", lambda command: launches.append(command))

    manager._retry_window_launch(child, ["AIUsageTray.exe", "--dashboard"])

    assert launches == [["AIUsageTray.exe", "--dashboard"]]
