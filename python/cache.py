"""
Cache manager with TTL support
"""
import json
import time
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from config import get_cache_dir


class CacheManager:
    """Manages caching of usage data with TTL"""
    
    def __init__(self, ttl_seconds: int = 55):
        self._ttl = ttl_seconds
        self._cache_dir = get_cache_dir()
    
    def get_cached(self, provider: str) -> Optional[dict]:
        """Get cached data if still valid"""
        cache_file = self._cache_dir / f"usage-cache-{provider}.json"
        
        if not cache_file.exists():
            return None
        
        # Check TTL
        file_age = time.time() - cache_file.stat().st_mtime
        if file_age < self._ttl:
            try:
                with open(cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                return None
        
        return None
    
    def save(self, provider: str, data: dict):
        """Save data to cache atomically"""
        cache_file = self._cache_dir / f"usage-cache-{provider}.json"
        temp_file = cache_file.with_suffix(".tmp")
        
        # Atomic write: write to temp file then move
        with open(temp_file, "w") as f:
            json.dump(data, f, indent=2)
        
        temp_file.replace(cache_file)
    
    def clear(self):
        """Clear all cached data"""
        for file in self._cache_dir.glob("*.json"):
            file.unlink()
