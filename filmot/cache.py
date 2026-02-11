"""Caching system for Filmot API responses."""

import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Any


class Cache:
    """Simple file-based cache with TTL support."""
    
    def __init__(self, cache_dir: str = ".filmot_cache", ttl: int = 3600):
        """
        Initialize the cache.
        
        Args:
            cache_dir: Directory to store cache files
            ttl: Time-to-live in seconds (default: 1 hour)
        """
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self.cache_dir.mkdir(exist_ok=True)
        self._auto_purge()
    
    def _auto_purge(self) -> None:
        """Silently remove expired entries on startup to prevent unbounded growth."""
        current_time = time.time()
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                if current_time - cached.get("timestamp", 0) > self.ttl:
                    cache_file.unlink()
            except (json.JSONDecodeError, IOError, OSError):
                try:
                    cache_file.unlink()
                except OSError:
                    pass

    def _get_cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Generate a unique cache key from endpoint and params."""
        # Sort params for consistent hashing
        sorted_params = json.dumps(params, sort_keys=True)
        key_string = f"{endpoint}:{sorted_params}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{cache_key}.json"
    
    def get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Retrieve a cached response if valid.
        
        Args:
            endpoint: API endpoint
            params: Request parameters
            
        Returns:
            Cached data if valid, None otherwise
        """
        cache_key = self._get_cache_key(endpoint, params)
        cache_path = self._get_cache_path(cache_key)
        
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            
            # Check if cache is still valid
            if time.time() - cached.get("timestamp", 0) > self.ttl:
                cache_path.unlink()  # Delete expired cache
                return None
            
            return cached.get("data")
        except (json.JSONDecodeError, IOError):
            return None
    
    def set(self, endpoint: str, params: Dict[str, Any], data: Dict[str, Any]) -> None:
        """
        Store a response in the cache.
        
        Args:
            endpoint: API endpoint
            params: Request parameters
            data: Response data to cache
        """
        cache_key = self._get_cache_key(endpoint, params)
        cache_path = self._get_cache_path(cache_key)
        
        cache_entry = {
            "timestamp": time.time(),
            "endpoint": endpoint,
            "params": params,
            "data": data
        }
        
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_entry, f, indent=2)
        except IOError:
            pass  # Silently fail if we can't write cache
    
    def clear(self) -> int:
        """
        Clear all cached data.
        
        Returns:
            Number of cache entries deleted
        """
        count = 0
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
                count += 1
            except IOError:
                pass
        return count
    
    def clear_expired(self) -> int:
        """
        Clear only expired cache entries.
        
        Returns:
            Number of expired entries deleted
        """
        count = 0
        current_time = time.time()
        
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                
                if current_time - cached.get("timestamp", 0) > self.ttl:
                    cache_file.unlink()
                    count += 1
            except (json.JSONDecodeError, IOError):
                # Delete corrupted cache files
                try:
                    cache_file.unlink()
                    count += 1
                except IOError:
                    pass
        
        return count
    
    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        total = 0
        valid = 0
        expired = 0
        size_bytes = 0
        current_time = time.time()
        
        for cache_file in self.cache_dir.glob("*.json"):
            total += 1
            size_bytes += cache_file.stat().st_size
            
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                
                if current_time - cached.get("timestamp", 0) > self.ttl:
                    expired += 1
                else:
                    valid += 1
            except (json.JSONDecodeError, IOError):
                expired += 1
        
        return {
            "total_entries": total,
            "valid_entries": valid,
            "expired_entries": expired,
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir),
            "ttl_seconds": self.ttl
        }


# Global cache instance
_cache: Optional[Cache] = None


def get_cache(ttl: int = 3600) -> Cache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = Cache(ttl=ttl)
    return _cache
