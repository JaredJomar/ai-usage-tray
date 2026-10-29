from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace

import settings_window as settings_module
import tray as tray_module
import config as config_module
from cache import CacheManager
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

    manager._check_reset_credit_notifications(now)
    manager._check_reset_credit_notifications(now)
    manager._check_reset_credit_notifications(now + timedelta(minutes=25))
    manager._check_reset_credit_notifications(now + timedelta(minutes=25))
    manager._check_reset_credit_notifications(now + timedelta(minutes=36))
    manager._check_reset_credit_notifications(now + timedelta(minutes=36))
    manager._check_reset_credit_notifications(now + timedelta(minutes=46))

    assert len(manager._tray_icon.notifications) == 4
    assert "60 minutes" in manager._tray_icon.notifications[0][0]
    assert "30 minutes" in manager._tray_icon.notifications[1][0]
    assert "15 minutes" in manager._tray_icon.notifications[2][0]
    assert "5 minutes" in manager._tray_icon.notifications[3][0]


def test_credit_first_seen_inside_fifteen_minutes_only_alerts_at_fifteen():
    manager = _manager_with_credit((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

    manager._check_notifications()

    assert len(manager._tray_icon.notifications) == 1
    assert "15 minutes" in manager._tray_icon.notifications[0][0]


def test_credit_first_seen_inside_five_minutes_only_alerts_at_five():
    manager = _manager_with_credit((datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat())

    manager._check_reset_credit_notifications(datetime.now(timezone.utc))

    assert len(manager._tray_icon.notifications) == 1
    assert "5 minutes" in manager._tray_icon.notifications[0][0]


def test_skipped_thresholds_emit_only_the_nearest_relevant_alert():
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now + timedelta(minutes=50)).isoformat())

    manager._check_reset_credit_notifications(now)
    manager._check_reset_credit_notifications(now + timedelta(minutes=48))

    assert len(manager._tray_icon.notifications) == 2
    assert "60 minutes" in manager._tray_icon.notifications[0][0]
    assert "5 minutes" in manager._tray_icon.notifications[1][0]


def test_alerts_are_disabled_when_notifications_are_off_or_no_credit_is_available():
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now + timedelta(minutes=2)).isoformat())

    manager._config.notifications_enabled = False
    manager._check_reset_credit_notifications(now)
    manager._config.notifications_enabled = True
    manager._codex_data["reset_credits"] = 0
    manager._check_reset_credit_notifications(now)

    assert manager._tray_icon.notifications == []


def test_expired_and_absent_credit_records_do_not_alert():
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now - timedelta(minutes=1)).isoformat())

    manager._check_reset_credit_notifications(now)
    manager._codex_data["reset_credit_expiries"] = []
    manager._check_reset_credit_notifications(now)

    assert manager._tray_icon.notifications == []


def test_status_ticks_check_credit_alerts_without_a_refresh():
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now + timedelta(minutes=50)).isoformat())
    manager._last_fetch = None
    manager._tray_icon = None

    manager._update_status_display()

    assert len(manager._credit_alerts_sent) == 1


def test_status_tick_does_not_alert_from_stale_data_after_cache_invalidation(
    tmp_path, monkeypatch,
):
    now = datetime.now(timezone.utc)
    manager = _manager_with_credit((now + timedelta(minutes=50)).isoformat())
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    manager._cache = CacheManager()

    manager._check_reset_credit_notifications(now)

    assert manager._tray_icon.notifications == []


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


def test_auto_start_menu_choice_persists_and_overrides_default(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(launch_on_startup=True)
    writes = []
    monkeypatch.setattr(TrayManager, "_is_auto_start_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(TrayManager, "_set_auto_start", staticmethod(writes.append))
    monkeypatch.setattr(tray_module, "save_config", lambda config: writes.append(config.launch_on_startup))

    manager._on_toggle_auto_start(None, None)

    assert writes == [False, False]
    assert manager._config.launch_on_startup is False


def test_auto_start_registry_commands_quote_program_files_paths(monkeypatch):
    executable = r"C:\Program Files\AI Usage Tray\AIUsageTray.exe"
    registry_values = []
    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER="hkcu",
        KEY_SET_VALUE=1,
        REG_SZ=1,
        OpenKey=lambda *args: "run-key",
        SetValueEx=lambda key, name, reserved, value_type, value: registry_values.append(value),
        CloseKey=lambda key: None,
    )
    monkeypatch.setattr(tray_module.sys, "executable", executable)
    monkeypatch.setattr(tray_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tray_module, "winreg", fake_winreg)
    monkeypatch.setattr(settings_module, "winreg", fake_winreg)

    TrayManager._set_auto_start(True)
    settings_module.SettingsWindow._set_auto_start(True)

    assert registry_values == ['"{}"'.format(executable), '"{}"'.format(executable)]


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


def test_dashboard_child_environment_uses_independent_pyinstaller_extraction(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:\\Temp\\_MEI123")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:\\Temp\\AIUsageTray.exe")
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")

    environment = TrayManager._child_environment()

    assert environment["_PYI_APPLICATION_HOME_DIR"] == "C:\\Temp\\_MEI123"
    assert environment["_PYI_ARCHIVE_FILE"] == "C:\\Temp\\AIUsageTray.exe"
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
