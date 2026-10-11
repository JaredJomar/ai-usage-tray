import json
import time

import providers.codex as codex_module
from cache import CacheEntry
from providers.codex import (
    CodexProvider,
    STATUS_API_ERROR,
    STATUS_AUTH_REQUIRED,
    STATUS_FRESH,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
)


class EntryCache:
    def __init__(self, entry=None):
        self.entry = entry
        self.saved = None

    def get_cached(self, provider):
        if self.entry is not None and self.entry.fresh:
            return self.entry.data
        return None

    def get_entry(self, provider):
        return self.entry

    def save(self, provider, data):
        self.saved = data
        self.entry = CacheEntry(dict(data), time.time(), True)


class Response:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


USAGE_PAYLOAD = {
    "rate_limit": {
        "primary_window": {"used_percent": 40, "reset_at": "2026-09-01T14:00:00Z"},
        "secondary_window": {"used_percent": 20, "reset_at": "2026-09-08T12:00:00Z"},
    },
    "rate_limit_reset_credits": {"available_count": 1},
    "plan_type": "pro",
}

PRIOR_USAGE = {
    "five_hour": 55.0,
    "five_hour_reset": "2026-09-01T14:00:00Z",
    "seven_day": 15.0,
    "seven_day_reset": "2026-09-08T12:00:00Z",
    "reset_credits": 1,
    "reset_credit_expiries": [],
}


def _write_auth(tmp_path, tokens=None):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": tokens if tokens is not None else {"access_token": "test-token"}}),
        encoding="utf-8",
    )
    return auth_path


def _patch(monkeypatch, tmp_path, auth_path=None):
    monkeypatch.setattr(
        codex_module, "get_codex_auth_path",
        lambda: auth_path if auth_path is not None else tmp_path / "missing-auth.json",
    )
    monkeypatch.setattr(codex_module, "get_ca_bundle_path", lambda: None)


def test_live_success_is_fresh_and_records_last_success(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))
    cache = EntryCache()
    provider = CodexProvider(cache)

    def fake_get(url, **kwargs):
        if url == provider.API_URL:
            return Response(200, USAGE_PAYLOAD)
        return Response(503, {})

    monkeypatch.setattr(codex_module.requests, "get", fake_get)

    before = time.time()
    result = provider.fetch_result()

    assert result.status == STATUS_FRESH
    assert result.usage["five_hour"] == 40.0
    assert result.usage["plan_type"] == "pro"
    assert result.usage["reset_credit_expiries"] == []
    assert result.last_success is not None and result.last_success >= before
    assert cache.saved == result.usage
    assert provider.fetch()["five_hour"] == 40.0


def test_fresh_cache_with_expiries_skips_network(monkeypatch, tmp_path):
    saved_at = time.time() - 10
    cache = EntryCache(CacheEntry(dict(PRIOR_USAGE), saved_at, True))
    provider = CodexProvider(cache)
    calls = []
    monkeypatch.setattr(codex_module.requests, "get", lambda url, **kwargs: calls.append(url))
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))

    result = provider.fetch_result()

    assert calls == []
    assert result.status == STATUS_FRESH
    assert result.usage == PRIOR_USAGE
    assert result.last_success == saved_at


def test_http_failure_with_prior_cache_is_stale_and_keeps_data(monkeypatch, tmp_path):
    saved_at = time.time() - 600
    cache = EntryCache(CacheEntry(dict(PRIOR_USAGE), saved_at, False))
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))
    monkeypatch.setattr(codex_module.requests, "get", lambda url, **kwargs: Response(503, {}))
    provider = CodexProvider(cache)

    result = provider.fetch_result()

    assert result.status == STATUS_STALE
    assert result.usage == PRIOR_USAGE
    assert result.last_success == saved_at
    assert "503" in result.detail
    assert provider.fetch() == PRIOR_USAGE


def test_network_error_without_cache_is_api_error(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))

    def fake_get(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(codex_module.requests, "get", fake_get)

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_API_ERROR
    assert result.usage is None
    assert result.last_success is None
    assert result.detail == "Network error"


def test_unauthorized_with_failed_refresh_is_auth_required_and_keeps_data(monkeypatch, tmp_path):
    saved_at = time.time() - 300
    cache = EntryCache(CacheEntry(dict(PRIOR_USAGE), saved_at, False))
    auth_path = _write_auth(tmp_path, {"access_token": "expired", "refresh_token": "refresh"})
    _patch(monkeypatch, tmp_path, auth_path)
    monkeypatch.setattr(codex_module.requests, "get", lambda url, **kwargs: Response(401, {}))
    monkeypatch.setattr(codex_module.requests, "post", lambda url, **kwargs: Response(400, {}))

    result = CodexProvider(cache).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage == PRIOR_USAGE
    assert result.last_success == saved_at
    assert result.detail == "Session expired"


def test_unauthorized_with_successful_refresh_retries_fetch(monkeypatch, tmp_path):
    auth_path = _write_auth(tmp_path, {"access_token": "expired", "refresh_token": "refresh"})
    _patch(monkeypatch, tmp_path, auth_path)
    cache = EntryCache()
    provider = CodexProvider(cache)
    responses = [Response(401, {}), Response(200, USAGE_PAYLOAD), Response(503, {})]
    monkeypatch.setattr(
        codex_module.requests, "post",
        lambda url, **kwargs: Response(200, {"access_token": "new-token", "expires_in": 3600}),
    )

    def fake_get(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(codex_module.requests, "get", fake_get)

    result = provider.fetch_result()

    assert result.status == STATUS_FRESH
    assert result.usage["plan_type"] == "pro"
    saved_auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert saved_auth["tokens"]["access_token"] == "new-token"


def test_missing_auth_with_prior_cache_is_auth_required_and_keeps_data(monkeypatch, tmp_path):
    saved_at = time.time() - 120
    cache = EntryCache(CacheEntry(dict(PRIOR_USAGE), saved_at, False))
    _patch(monkeypatch, tmp_path, None)

    result = CodexProvider(cache).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage == PRIOR_USAGE
    assert result.last_success == saved_at
    assert result.detail == "Not logged in"


def test_missing_auth_without_cache_is_auth_required_with_no_data(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, None)

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage is None
    assert result.last_success is None


def test_unreadable_auth_file_is_auth_required(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("{broken", encoding="utf-8")
    _patch(monkeypatch, tmp_path, auth_path)

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage is None


def test_malformed_auth_tokens_null_is_auth_required(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": None}), encoding="utf-8")
    _patch(monkeypatch, tmp_path, auth_path)

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage is None


def test_malformed_auth_root_is_auth_required(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    _patch(monkeypatch, tmp_path, auth_path)

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_AUTH_REQUIRED
    assert result.usage is None


def test_cache_save_failure_keeps_fresh_result(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))

    class FailingSaveCache(EntryCache):
        def save(self, provider, data):
            raise OSError("disk full")

    cache = FailingSaveCache()
    provider = CodexProvider(cache)

    def fake_get(url, **kwargs):
        if url == provider.API_URL:
            return Response(200, USAGE_PAYLOAD)
        return Response(503, {})

    monkeypatch.setattr(codex_module.requests, "get", fake_get)

    result = provider.fetch_result()

    assert result.status == STATUS_FRESH
    assert result.usage["five_hour"] == 40.0
    assert result.usage["reset_credit_expiries"] == []
    assert result.last_success is not None
    assert cache.saved is None


def test_unparseable_response_without_cache_is_unavailable(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))

    class BadResponse:
        status_code = 200

        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr(codex_module.requests, "get", lambda url, **kwargs: BadResponse())

    result = CodexProvider(EntryCache()).fetch_result()

    assert result.status == STATUS_UNAVAILABLE
    assert result.usage is None


def test_stale_fallback_requires_usable_usage_keys(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, _write_auth(tmp_path))
    cache = EntryCache(CacheEntry({"five_hour": 10.0}, time.time() - 60, False))
    monkeypatch.setattr(codex_module.requests, "get", lambda url, **kwargs: Response(500, {}))

    result = CodexProvider(cache).fetch_result()

    assert result.status == STATUS_API_ERROR
    assert result.usage is None


def test_fetch_returns_none_when_auth_required_without_data(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, None)

    assert CodexProvider(EntryCache()).fetch() is None
