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


class FakeChild:
    def __init__(self, exit_code=None):
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


def test_dashboard_launch_retries_with_backoff_until_healthy(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    launches = []

    def fake_popen(command, **kwargs):
        launches.append(command)
        return FakeChild(exit_code=None)

    monkeypatch.setattr(tray_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(tray_module.subprocess, "Popen", fake_popen)

    manager._retry_window_launch(FakeChild(exit_code=1), ["AIUsageTray.exe", "--dashboard"])

    assert launches == [["AIUsageTray.exe", "--dashboard"]]


def test_dashboard_launch_gives_up_after_repeated_startup_failures(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    launches = []
    sleeps = []

    def fake_popen(command, **kwargs):
        launches.append(command)
        return FakeChild(exit_code=1)

    monkeypatch.setattr(tray_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(tray_module.subprocess, "Popen", fake_popen)

    manager._retry_window_launch(FakeChild(exit_code=1), ["AIUsageTray.exe", "--dashboard"])

    assert launches == [["AIUsageTray.exe", "--dashboard"]] * 3
    assert sleeps == [2, 5, 10]


def test_dashboard_launch_ignores_clean_exit(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    launches = []

    monkeypatch.setattr(tray_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        tray_module.subprocess, "Popen", lambda command, **kwargs: launches.append(command)
    )

    manager._retry_window_launch(FakeChild(exit_code=0), ["AIUsageTray.exe", "--dashboard"])

    assert launches == []


def test_dashboard_launch_is_debounced(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._window_launch_lock = threading.Lock()
    manager._last_dashboard_launch = 0.0
    launches = []

    monkeypatch.setattr(tray_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        tray_module.subprocess, "Popen", lambda command, **kwargs: launches.append(command) or FakeChild()
    )
    monkeypatch.setattr(TrayManager, "_open_child_log", staticmethod(lambda: None))
    monkeypatch.setattr(TrayManager, "_get_cursor_position", staticmethod(lambda: None))

    manager._launch_window("--dashboard")
    manager._launch_window("--dashboard")

    assert len(launches) == 1


def test_dashboard_child_environment_keeps_the_existing_pyinstaller_home(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:\\Temp\\_MEI123")
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "1")

    environment = TrayManager._child_environment()

    assert environment["_PYI_APPLICATION_HOME_DIR"] == "C:\\Temp\\_MEI123"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in environment
