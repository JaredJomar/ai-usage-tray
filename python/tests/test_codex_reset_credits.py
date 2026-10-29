import json
import time
from datetime import datetime, timedelta, timezone

import providers.codex as codex_module
from cache import CacheEntry
from providers.codex import CodexProvider


class MemoryCache:
    def __init__(self, cached=None):
        self.cached = cached
        self.saved = None
        self.cleared_provider = None

    def get_cached(self, provider):
        return self.cached

    def get_entry(self, provider):
        if self.cached is None:
            return None
        return CacheEntry(dict(self.cached), time.time() - 3600, False)

    def save(self, provider, data):
        self.saved = data

    def clear_provider(self, provider):
        self.cleared_provider = provider


class Response:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_parse_reset_credit_expiries_normalizes_available_future_values():
    now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    expiries = CodexProvider._parse_reset_credit_expiries(
        {
            "credits": [
                {"id": "iso", "status": "available", "expires_at": "2026-09-04T13:00:00Z"},
                {"id": "seconds", "status": "available", "expires_at": 1788528600},
                {"id": "milliseconds", "status": "AVAILABLE", "expires_at": 1788528600000},
                {"id": "used", "status": "used", "expires_at": "2026-09-04T14:00:00Z"},
                {"id": "expired", "status": "available", "expires_at": "2026-09-04T11:59:00Z"},
                {"id": "bad", "status": "available", "expires_at": "not-a-timestamp"},
                {"id": "null", "status": "available", "expires_at": None},
            ],
        },
        now,
    )

    assert expiries == [
        {"id": "iso", "expires_at": "2026-09-04T13:00:00Z"},
        {"id": "milliseconds", "expires_at": "2026-09-04T13:30:00Z"},
        {"id": "seconds", "expires_at": "2026-09-04T13:30:00Z"},
    ]


def test_fetch_refreshes_legacy_cache_and_keeps_usage_when_detail_request_fails(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": "test-token"}}), encoding="utf-8")
    cache = MemoryCache({"five_hour": 10, "reset_credits": 2})
    provider = CodexProvider(cache)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == provider.API_URL:
            return Response(200, {
                "rate_limit": {"primary_window": {}, "secondary_window": {}},
                "rate_limit_reset_credits": {"available_count": 2},
            })
        return Response(503, {})

    monkeypatch.setattr(codex_module, "get_codex_auth_path", lambda: auth_path)
    monkeypatch.setattr(codex_module, "get_ca_bundle_path", lambda: "test-ca.pem")
    monkeypatch.setattr(codex_module.requests, "get", fake_get)

    usage = provider.fetch()

    assert calls == [provider.API_URL, provider.RESET_CREDITS_URL]
    assert usage["reset_credits"] == 2
    assert usage["reset_credit_expiries"] == []
    assert usage["reset_credits_expires_at"] is None


def test_consume_reset_credit_posts_idempotent_request_with_optional_account_id(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"account_id": "acct-1", "tokens": {"access_token": "test-token"}}),
        encoding="utf-8",
    )
    provider = CodexProvider(MemoryCache())
    requests = []

    def fake_post(url, **kwargs):
        requests.append((url, kwargs))
        return Response(200, {"outcome": "reset"})

    monkeypatch.setattr(codex_module, "get_codex_auth_path", lambda: auth_path)
    monkeypatch.setattr(codex_module, "get_ca_bundle_path", lambda: "test-ca.pem")
    monkeypatch.setattr(codex_module.requests, "post", fake_post)

    result = provider.consume_reset_credit("request-1", "credit-1")

    assert result.status == "reset"
    assert provider._cache.cleared_provider == "codex"
    assert requests == [(
        provider.RESET_CREDITS_CONSUME_URL,
        {
            "headers": {
                "Authorization": "Bearer test-token",
                "User-Agent": provider.USER_AGENT,
                "Content-Type": "application/json",
                "ChatGPT-Account-Id": "acct-1",
            },
            "json": {"redeem_request_id": "request-1", "credit_id": "credit-1"},
            "timeout": 15,
            "verify": "test-ca.pem",
        },
    )]


def test_consume_reset_credit_preserves_server_rejection(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": "test-token"}}), encoding="utf-8")
    provider = CodexProvider(MemoryCache())
    monkeypatch.setattr(codex_module, "get_codex_auth_path", lambda: auth_path)
    monkeypatch.setattr(codex_module, "get_ca_bundle_path", lambda: "test-ca.pem")
    monkeypatch.setattr(
        codex_module.requests, "post", lambda url, **kwargs: Response(403, {"error": "not eligible"}),
    )

    result = provider.consume_reset_credit("request-1", "credit-1")

    assert result.status == "api_error"
    assert result.detail == "API returned HTTP 403"


def test_consume_reset_credit_does_not_assume_success_for_unknown_response(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": "test-token"}}), encoding="utf-8")
    provider = CodexProvider(MemoryCache())
    monkeypatch.setattr(codex_module, "get_codex_auth_path", lambda: auth_path)
    monkeypatch.setattr(codex_module, "get_ca_bundle_path", lambda: "test-ca.pem")
    monkeypatch.setattr(
        codex_module.requests, "post", lambda url, **kwargs: Response(200, {}),
    )

    result = provider.consume_reset_credit("request-1", "credit-1")

    assert result.status == "api_error"
    assert result.detail == "Unexpected API response"
