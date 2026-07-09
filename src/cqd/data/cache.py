"""Disk cache with TTLs for slow API responses."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from diskcache import Cache

_CACHE_DIR = Path.home() / ".cqd" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

cache = Cache(str(_CACHE_DIR))

T = TypeVar("T")


def cached(key_prefix: str, ttl_seconds: int):
    """Decorator: cache async function results on disk with a TTL.

    Cache key combines the prefix with args/kwargs.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            full_key = f"{key_prefix}:{args}:{tuple(sorted(kwargs.items()))}"
            hit = cache.get(full_key)
            if hit is not None:
                return hit  # type: ignore[return-value]
            result = await fn(*args, **kwargs)
            cache.set(full_key, result, expire=ttl_seconds)
            return result

        return wrapper

    return decorator
