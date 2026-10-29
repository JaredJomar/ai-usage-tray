"""
Cache manager with TTL support
"""
import json
import time
from math import isfinite
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from config import get_cache_dir

CACHE_META_KEY = "_meta"


@dataclass(frozen=True)
class CacheEntry:
    """Cached payload plus freshness metadata for quota status displays."""

    data: dict
    saved_at: float
    fresh: bool


class CacheManager:
    """Manages caching of usage data with TTL"""
    
    def __init__(self, ttl_seconds: int = 55):
        self._ttl = ttl_seconds
        self._cache_dir = get_cache_dir()
    
    def get_cached(self, provider: str) -> Optional[dict]:
        """Get cached data if still valid"""
        entry = self.get_entry(provider)
        if entry is not None and entry.fresh:
            return entry.data
        return None

    def get_entry(self, provider: str) -> Optional[CacheEntry]:
        """Get cached data at any age with its last-success timestamp.

        Legacy cache files without the additive metadata key stay readable;
        their file modification time stands in for the last-success time.
        """
        cache_file = self._cache_dir / f"usage-cache-{provider}.json"

        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r") as f:
                payload = json.load(f)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        data = dict(payload)
        saved_at = self._saved_at_from_meta(data.pop(CACHE_META_KEY, None))
        if saved_at is None:
            saved_at = cache_file.stat().st_mtime

        age = time.time() - saved_at
        return CacheEntry(data=data, saved_at=saved_at, fresh=age < self._ttl)

    @staticmethod
    def _saved_at_from_meta(meta: object) -> Optional[float]:
        """Read the additive last-success timestamp, tolerating legacy files."""
        if not isinstance(meta, dict):
            return None
        value = meta.get("last_success")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if not isfinite(value):
            return None
        return value
    
    def save(self, provider: str, data: dict):
        """Save data to cache atomically with an additive last-success timestamp"""
        cache_file = self._cache_dir / f"usage-cache-{provider}.json"
        temp_file = cache_file.with_suffix(".tmp")

        payload = dict(data)
        payload[CACHE_META_KEY] = {"last_success": time.time()}
        
        # Atomic write: write to temp file then move
        with open(temp_file, "w") as f:
            json.dump(payload, f, indent=2)
        
        temp_file.replace(cache_file)

    def clear_provider(self, provider: str) -> None:
        cache_file = self._cache_dir / f"usage-cache-{provider}.json"
        try:
            cache_file.unlink()
        except FileNotFoundError:
            pass
    
    def clear(self):
        """Clear all cached data"""
        for file in self._cache_dir.glob("*.json"):
            file.unlink()
