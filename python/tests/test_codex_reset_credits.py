import json
from datetime import datetime, timedelta, timezone

import providers.codex as codex_module
from providers.codex import CodexProvider


class MemoryCache:
    def __init__(self, cached=None):
        self.cached = cached
        self.saved = None

    def get_cached(self, provider):
        return self.cached

    def save(self, provider, data):
        self.saved = data


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
    assert cache.saved == usage
