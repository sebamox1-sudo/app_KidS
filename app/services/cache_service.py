import os
import json
import logging
from typing import Any, Callable, Optional
import redis.asyncio as aioredis
import redis as sync_redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Pool sincrono (per endpoint sync esistenti) e async (per nuovi endpoint async)
_sync_pool = sync_redis.ConnectionPool.from_url(
    REDIS_URL, max_connections=50, decode_responses=True
)
_async_pool: Optional[aioredis.ConnectionPool] = None


def get_sync_redis() -> sync_redis.Redis:
    return sync_redis.Redis(connection_pool=_sync_pool)


async def get_async_redis() -> aioredis.Redis:
    global _async_pool
    if _async_pool is None:
        _async_pool = aioredis.ConnectionPool.from_url(
            REDIS_URL, max_connections=50, decode_responses=True
        )
    return aioredis.Redis(connection_pool=_async_pool)


# ============================================================
# CACHE-ASIDE — sincrono (per i router attuali)
# ============================================================
def cache_get_or_set(
    key: str,
    ttl_seconds: int,
    loader: Callable[[], Any],
) -> Any:
    """Ritorna il valore da Redis o lo calcola e lo memorizza.
    
    Graceful degradation: se Redis è down, calcola comunque.
    """
    r = get_sync_redis()
    try:
        cached = r.get(key)
        if cached is not None:
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Cache read miss (redis down?): {e}")

    valore = loader()

    try:
        r.setex(key, ttl_seconds, json.dumps(valore, default=str))
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")

    return valore


def cache_invalidate(*keys: str) -> None:
    """Invalida una o più chiavi. Safe anche se Redis è down."""
    if not keys:
        return
    r = get_sync_redis()
    try:
        r.delete(*keys)
    except Exception as e:
        logger.warning(f"Cache invalidate failed: {e}")


def cache_invalidate_pattern(pattern: str) -> None:
    """Invalida tutte le chiavi che matchano un pattern (es. 'profilo:*')."""
    r = get_sync_redis()
    try:
        # SCAN è non-bloccante, meglio di KEYS in prod
        for key in r.scan_iter(match=pattern, count=500):
            r.delete(key)
    except Exception as e:
        logger.warning(f"Cache pattern invalidate failed: {e}")