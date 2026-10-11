import json
import os
import time

import config
from cache import CACHE_META_KEY, CacheManager


USAGE = {
    "five_hour": 12.0,
    "five_hour_reset": "2026-09-01T14:00:00Z",
    "seven_day": 34.0,
    "seven_day_reset": "2026-09-08T12:00:00Z",
}


def _manager(tmp_path, monkeypatch, ttl=55):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return CacheManager(ttl)


def test_save_stores_additive_last_success_and_reads_back_fresh_entry(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    before = time.time()
    manager.save("codex", USAGE)
    after = time.time()

    entry = manager.get_entry("codex")
    assert entry is not None
    assert entry.fresh is True
    assert entry.data == USAGE
    assert before <= entry.saved_at <= after
    assert manager.get_cached("codex") == USAGE


def test_saved_payload_keeps_caller_dict_untouched_and_meta_hidden_on_read(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    data = dict(USAGE)

    manager.save("codex", data)

    assert data == USAGE
    raw = json.loads((tmp_path / "cache" / "usage-cache-codex.json").read_text(encoding="utf-8"))
    assert CACHE_META_KEY in raw
    assert CACHE_META_KEY not in manager.get_cached("codex")


def test_legacy_cache_file_without_meta_stays_readable(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    cache_file = tmp_path / "cache" / "usage-cache-codex.json"
    cache_file.write_text(json.dumps(USAGE), encoding="utf-8")
    legacy_time = time.time() - 3600
    os.utime(cache_file, (legacy_time, legacy_time))

    entry = manager.get_entry("codex")

    assert entry is not None
    assert entry.data == USAGE
    assert entry.fresh is False
    assert abs(entry.saved_at - legacy_time) < 1
    assert manager.get_cached("codex") is None


def test_expired_entry_is_stale_but_still_retrievable(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, ttl=1)
    manager.save("codex", USAGE)
    cache_file = tmp_path / "cache" / "usage-cache-codex.json"
    old = time.time() - 120
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    raw[CACHE_META_KEY] = {"last_success": old}
    cache_file.write_text(json.dumps(raw), encoding="utf-8")

    entry = manager.get_entry("codex")

    assert entry is not None
    assert entry.fresh is False
    assert entry.data == USAGE
    assert abs(entry.saved_at - old) < 1
    assert manager.get_cached("codex") is None


def test_corrupt_cache_file_is_unreadable(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    (tmp_path / "cache" / "usage-cache-codex.json").write_text("{not json", encoding="utf-8")

    assert manager.get_entry("codex") is None
    assert manager.get_cached("codex") is None


def test_non_finite_last_success_falls_back_to_mtime(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.save("codex", USAGE)
    cache_file = tmp_path / "cache" / "usage-cache-codex.json"
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    raw[CACHE_META_KEY] = {"last_success": float("nan")}
    cache_file.write_text(json.dumps(raw), encoding="utf-8")
    file_time = time.time() - 120
    os.utime(cache_file, (file_time, file_time))

    entry = manager.get_entry("codex")

    assert entry is not None
    assert entry.data == USAGE
    assert abs(entry.saved_at - file_time) < 1
    assert entry.fresh is False


def test_infinite_last_success_falls_back_to_mtime(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.save("codex", USAGE)
    cache_file = tmp_path / "cache" / "usage-cache-codex.json"
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    raw[CACHE_META_KEY] = {"last_success": float("inf")}
    cache_file.write_text(json.dumps(raw), encoding="utf-8")
    file_time = time.time() - 30
    os.utime(cache_file, (file_time, file_time))

    entry = manager.get_entry("codex")

    assert entry is not None
    assert abs(entry.saved_at - file_time) < 1
