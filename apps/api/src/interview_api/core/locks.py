"""Redis distributed lock helpers."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from interview_api.core.errors import ResourceLockedError
from interview_api.core.rate_limit import get_redis_client

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


async def acquire_lock(key: str, ttl_seconds: int) -> str | None:
    token = str(uuid.uuid4())
    client = get_redis_client()
    ok = await client.set(f"lock:{key}", token, nx=True, ex=ttl_seconds)
    return token if ok else None


async def release_lock(key: str, token: str) -> bool:
    client = get_redis_client()
    result = await client.eval(_RELEASE_SCRIPT, 1, f"lock:{key}", token)
    return bool(result)


@asynccontextmanager
async def redis_lock(key: str, ttl_seconds: int):
    token = await acquire_lock(key, ttl_seconds)
    if not token:
        raise ResourceLockedError()
    try:
        yield token
    finally:
        await release_lock(key, token)
