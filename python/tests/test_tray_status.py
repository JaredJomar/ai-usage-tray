import threading
import time
from datetime import datetime, timedelta, timezone

from config import AppConfig
from providers.codex import (
    FetchResult,
    STATUS_API_ERROR,
    STATUS_AUTH_REQUIRED,
    STATUS_FRESH,
    STATUS_STALE,
)
from tray import TrayManager


def _usage():
    now = datetime.now(timezone.utc)
    return {
        "five_hour": 86,
        "five_hour_reset": (now + timedelta(hours=3, minutes=8)).isoformat(),
        "seven_day": 34,
        "seven_day_reset": (now + timedelta(days=5, hours=1)).isoformat(),
        "reset_credits": 1,
        "reset_credit_expiries": [
            {"id": "credit-1", "expires_at": (now + timedelta(minutes=42)).isoformat()},
        ],
    }


def _manager(data=None, result=None, refreshing=False):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig()
    manager._codex_data = data
    manager._last_fetch = result
    manager._refreshing = refreshing
    manager._credit_alerts_sent = set()
    manager._credit_alert_lock = threading.Lock()
    return manager


def test_tooltip_fresh_status_keeps_usage_rows_and_reset_details():
    manager = _manager(_usage(), FetchResult(STATUS_FRESH, _usage(), time.time() - 600))

    tooltip = manager._build_tooltip()

    assert "CODEX USAGE" in tooltip
    assert "5h |" in tooltip
    assert "7d |" in tooltip
    assert "RESETS: 1" in tooltip
    assert "expires" in tooltip
    assert "Updated 10m ago" in tooltip
    assert len(tooltip) <= TrayManager.TOOLTIP_MAX_LENGTH


def test_tooltip_stale_status_shows_age_and_keeps_rows():
    manager = _manager(
        _usage(), FetchResult(STATUS_STALE, _usage(), time.time() - 1800, "API returned HTTP 503"),
    )

    tooltip = manager._build_tooltip()

    assert "Stale · 30m ago" in tooltip
    assert "RESETS: 1" in tooltip
    assert len(tooltip) <= TrayManager.TOOLTIP_MAX_LENGTH


def test_tooltip_auth_required_keeps_rows_and_fits_length_limit():
    manager = _manager(
        _usage(), FetchResult(STATUS_AUTH_REQUIRED, _usage(), time.time() - 300, "Session expired"),
    )

    tooltip = manager._build_tooltip()

    assert "Login required" in tooltip
    assert "5h |" in tooltip
    assert "RESETS: 1" in tooltip
    assert len(tooltip) <= TrayManager.TOOLTIP_MAX_LENGTH


def test_tooltip_auth_required_without_data_hints_login():
    manager = _manager(None, FetchResult(STATUS_AUTH_REQUIRED, None, None, "Not logged in"))

    tooltip = manager._build_tooltip()

    assert "Login required" in tooltip
    assert "No data" in tooltip
    assert "Right-click > Login" in tooltip


def test_tooltip_api_error_without_data_hints_refresh_not_login():
    manager = _manager(None, FetchResult(STATUS_API_ERROR, None, None, "API returned HTTP 503"))

    tooltip = manager._build_tooltip()

    assert "Refresh failed" in tooltip
    assert "Right-click > Refresh Now" in tooltip
    assert "Login" not in tooltip


def test_tooltip_unavailable_without_data_says_unavailable():
    manager = _manager(None, FetchResult("unavailable", None, None, "Unexpected API response"))

    tooltip = manager._build_tooltip()

    assert "Unavailable" in tooltip
    assert "Right-click > Refresh Now" in tooltip


def test_tooltip_refreshing_and_loading_states():
    refreshing = _manager(_usage(), refreshing=True)
    loading = _manager(None)

    assert "Refreshing" in refreshing._build_tooltip()
    assert "Loading" in loading._build_tooltip()


def test_refresh_usage_keeps_prior_data_and_skips_notifications_on_failure(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=True)
    prior = _usage()
    manager._codex_data = prior
    manager._last_fetch = None
    manager._refreshing = False
    manager._codex = type("FakeCodex", (), {})()
    manager._codex.fetch_result = lambda: FetchResult(
        STATUS_STALE, prior, time.time() - 900, "API returned HTTP 500",
    )
    updated = []
    notifications = []
    monkeypatch.setattr(manager, "_update_icon", lambda: updated.append("icon"))
    monkeypatch.setattr(manager, "_update_tooltip", lambda: updated.append("tooltip"))
    monkeypatch.setattr(manager, "_check_notifications", lambda: notifications.append(True))

    manager._refresh_usage()

    assert manager._codex_data is prior
    assert manager._last_fetch.status == STATUS_STALE
    assert manager._refreshing is False
    assert notifications == []
    assert updated == ["tooltip", "icon", "tooltip"]


def test_refresh_usage_success_updates_data_and_checks_notifications(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=True)
    fresh_usage = _usage()
    manager._codex_data = None
    manager._last_fetch = None
    manager._refreshing = False
    manager._codex = type("FakeCodex", (), {})()
    manager._codex.fetch_result = lambda: FetchResult(STATUS_FRESH, fresh_usage, time.time())
    notifications = []
    monkeypatch.setattr(manager, "_update_icon", lambda: None)
    monkeypatch.setattr(manager, "_update_tooltip", lambda: None)
    monkeypatch.setattr(manager, "_check_notifications", lambda: notifications.append(True))

    manager._refresh_usage()

    assert manager._codex_data is fresh_usage
    assert manager._last_fetch.status == STATUS_FRESH
    assert manager._refreshing is False
    assert notifications == [True]


def test_tooltip_worst_case_fits_and_keeps_status_age():
    now = datetime.now(timezone.utc)
    data = {
        "five_hour": 100,
        "five_hour_reset": (now + timedelta(days=12, hours=13)).isoformat(),
        "seven_day": 100,
        "seven_day_reset": (now + timedelta(days=12, hours=13)).isoformat(),
        "reset_credits": 12,
        "reset_credit_expiries": [
            {"id": "credit-1", "expires_at": (now + timedelta(days=12, hours=13)).isoformat()},
        ],
    }
    manager = _manager(
        data,
        FetchResult(STATUS_AUTH_REQUIRED, data, time.time() - 12 * 86400 - 13 * 3600, "Session expired"),
    )

    tooltip = manager._build_tooltip()

    assert len(tooltip) <= TrayManager.TOOLTIP_MAX_LENGTH
    assert "Login required · 12d ago" in tooltip
    assert "RESETS: 12" in tooltip
    assert "expires" not in tooltip
    assert "5h |" in tooltip
    assert "7d |" in tooltip


def test_tooltip_fallback_leads_with_status_when_nothing_fits(monkeypatch):
    manager = _manager(_usage(), FetchResult(STATUS_API_ERROR, _usage(), None, "API returned HTTP 503"))
    monkeypatch.setattr(TrayManager, "TOOLTIP_MAX_LENGTH", 40)

    tooltip = manager._build_tooltip()

    assert tooltip.startswith("Refresh failed")
    assert len(tooltip) <= 40


def test_refresh_usage_skips_when_a_refresh_is_already_in_flight():
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=False)
    manager._codex_data = None
    manager._last_fetch = None
    manager._refreshing = False
    fetches = []
    manager._codex = type("FakeCodex", (), {})()
    manager._codex.fetch_result = lambda: fetches.append(True)
    lock = manager._ensure_refresh_lock()
    lock.acquire()
    try:
        manager._refresh_usage()
    finally:
        lock.release()

    assert fetches == []
    assert manager._last_fetch is None
    assert manager._refreshing is False


def test_refresh_usage_exception_with_prior_data_becomes_stale(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=False)
    prior = _usage()
    prior_success = time.time() - 600
    manager._codex_data = prior
    manager._last_fetch = FetchResult(STATUS_FRESH, prior, prior_success)
    manager._refreshing = False
    manager._codex = type("FakeCodex", (), {})()

    def boom():
        raise RuntimeError("network down")

    manager._codex.fetch_result = boom
    monkeypatch.setattr(manager, "_update_icon", lambda: None)
    monkeypatch.setattr(manager, "_update_tooltip", lambda: None)

    manager._refresh_usage()

    assert manager._last_fetch.status == STATUS_STALE
    assert manager._last_fetch.usage is prior
    assert manager._last_fetch.last_success == prior_success
    assert "network down" in manager._last_fetch.detail
    assert manager._refreshing is False
    lock = manager._ensure_refresh_lock()
    assert lock.acquire(blocking=False)
    lock.release()


def test_refresh_usage_exception_without_data_becomes_api_error(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=False)
    manager._codex_data = None
    manager._last_fetch = None
    manager._refreshing = False
    manager._codex = type("FakeCodex", (), {})()

    def boom():
        raise RuntimeError("network down")

    manager._codex.fetch_result = boom
    monkeypatch.setattr(manager, "_update_icon", lambda: None)
    monkeypatch.setattr(manager, "_update_tooltip", lambda: None)

    manager._refresh_usage()

    assert manager._last_fetch.status == STATUS_API_ERROR
    assert manager._last_fetch.usage is None
    assert "network down" in manager._last_fetch.detail


def test_update_status_display_advances_age_without_fetching():
    manager = _manager(_usage(), FetchResult(STATUS_FRESH, _usage(), time.time() - 60))
    fake_icon = type("FakeIcon", (), {"title": ""})()
    manager._tray_icon = fake_icon
    fetches = []
    manager._codex = type("FakeCodex", (), {})()
    manager._codex.fetch_result = lambda: fetches.append(True)

    manager._update_status_display()

    assert fetches == []
    assert "Updated 1m ago" in fake_icon.title


def test_update_status_display_is_noop_without_data_or_result():
    manager = _manager(None, None)

    manager._update_status_display()

    assert manager._last_fetch is None


def test_normalize_failed_result_retains_prior_quota_for_api_error():
    prior = _usage()
    prior_success = time.time() - 600
    manager = _manager(prior, FetchResult(STATUS_FRESH, prior, prior_success))

    normalized = manager._normalize_failed_result(
        FetchResult(STATUS_API_ERROR, None, None, "Network error"),
    )

    assert normalized.status == STATUS_STALE
    assert normalized.usage is prior
    assert normalized.last_success == prior_success
    assert normalized.detail == "Network error"


def test_normalize_failed_result_keeps_auth_required_with_prior_quota():
    prior = _usage()
    prior_success = time.time() - 300
    manager = _manager(prior, FetchResult(STATUS_FRESH, prior, prior_success))

    normalized = manager._normalize_failed_result(
        FetchResult(STATUS_AUTH_REQUIRED, None, None, "Session expired"),
    )

    assert normalized.status == STATUS_AUTH_REQUIRED
    assert normalized.usage is prior
    assert normalized.last_success == prior_success


def test_normalize_failed_result_without_prior_data_returns_unchanged():
    manager = _manager(None, None)
    result = FetchResult(STATUS_API_ERROR, None, None, "Network error")

    assert manager._normalize_failed_result(result) is result


def test_normalize_failed_result_without_last_fetch_uses_none_timestamp():
    prior = _usage()
    manager = _manager(prior, None)

    normalized = manager._normalize_failed_result(
        FetchResult(STATUS_API_ERROR, None, None, "Network error"),
    )

    assert normalized.status == STATUS_STALE
    assert normalized.usage is prior
    assert normalized.last_success is None


def test_refresh_usage_normalizes_failed_result_with_prior_data(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=False)
    prior = _usage()
    prior_success = time.time() - 600
    manager._codex_data = prior
    manager._last_fetch = FetchResult(STATUS_FRESH, prior, prior_success)
    manager._refreshing = False
    manager._codex = type("FakeCodex", (), {})()
    manager._codex.fetch_result = lambda: FetchResult(
        STATUS_API_ERROR, None, None, "Network error",
    )
    monkeypatch.setattr(manager, "_update_icon", lambda: None)
    monkeypatch.setattr(manager, "_update_tooltip", lambda: None)

    manager._refresh_usage()

    assert manager._last_fetch.status == STATUS_STALE
    assert manager._last_fetch.usage is prior
    assert manager._last_fetch.last_success == prior_success


def test_overlapping_refresh_from_two_threads_fetches_once(monkeypatch):
    manager = TrayManager.__new__(TrayManager)
    manager._config = AppConfig(codex_enabled=True, notifications_enabled=False)
    manager._codex_data = None
    manager._last_fetch = None
    manager._refreshing = False
    fetch_entered = threading.Event()
    fetch_release = threading.Event()
    fetches = []
    manager._codex = type("FakeCodex", (), {})()

    def blocking_fetch():
        fetches.append(True)
        fetch_entered.set()
        assert fetch_release.wait(timeout=5)
        return FetchResult(STATUS_FRESH, _usage(), time.time())

    manager._codex.fetch_result = blocking_fetch
    monkeypatch.setattr(manager, "_update_icon", lambda: None)
    monkeypatch.setattr(manager, "_update_tooltip", lambda: None)

    worker = threading.Thread(target=manager._refresh_usage)
    worker.start()
    assert fetch_entered.wait(timeout=5)
    manager._refresh_usage()
    fetch_release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert fetches == [True]
    assert manager._last_fetch.status == STATUS_FRESH


def test_format_age_rejects_non_finite_timestamps():
    assert TrayManager._format_age(float("nan")) is None
    assert TrayManager._format_age(float("inf")) is None
    assert TrayManager._format_age(float("-inf")) is None
