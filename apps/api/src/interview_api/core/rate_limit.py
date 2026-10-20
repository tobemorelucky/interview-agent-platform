"""Redis-backed rate limiting dependencies."""

import logging
import asyncio
import time
import uuid
from hashlib import sha256
from collections.abc import Callable
from weakref import WeakKeyDictionary

import redis.asyncio as redis
from fastapi import Depends, Request

from interview_api.api.deps import get_current_user
from interview_api.core.config import settings
from interview_api.core.errors import AppError, RateLimitExceededError
from interview_api.modules.users.models import User

logger = logging.getLogger(__name__)

_redis_clients = WeakKeyDictionary()


def get_redis_client() -> redis.Redis:
    loop = asyncio.get_running_loop()
    if loop not in _redis_clients:
        _redis_clients[loop] = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_clients[loop]


async def close_rate_limit_client():
    client = _redis_clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


async def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    client = get_redis_client()
    now_ms = int(time.time() * 1000)
    window_start = now_ms - window_seconds * 1000
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {f"{now_ms}:{uuid.uuid4()}": now_ms})
        pipe.expire(key, window_seconds + 10)
        results = await pipe.execute()
        current_count = int(results[1]) + 1
        return current_count <= limit
    except Exception:
        if settings.app_env.lower() in {"development", "dev", "local"}:
            logger.warning("Redis unavailable for rate limit; allowing request", exc_info=True)
            return True
        raise AppError("RATE_LIMIT_UNAVAILABLE", "限流服务暂不可用，请稍后重试", 503)


def rate_limit(
    namespace: str,
    limit: int,
    window_seconds: int,
) -> Callable:
    async def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> None:
        key = f"rate:{namespace}:user:{current_user.id}"
        allowed = await check_rate_limit(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            raise RateLimitExceededError()

    return dependency


normal_user_api_limit = rate_limit("normal", 60, 60)


async def auth_entry_limit(request: Request) -> None:
    # Do not trust client-provided X-Forwarded-For without a trusted proxy config.
    peer = request.client.host if request.client else "unknown"
    key = "rate:auth:" + sha256(peer.encode()).hexdigest()
    if not await check_rate_limit(key, limit=30, window_seconds=600):
        raise RateLimitExceededError("登录或注册请求过于频繁，请稍后重试")
interview_chat_limit = rate_limit("interview_chat", 20, 60)
memory_write_limit = rate_limit("memory_write", 30, 60)
experience_task_limit = rate_limit("experience_task", 20, 86400)
search_run_limit = rate_limit("search_run", 30, 86400)
fetch_run_limit = rate_limit("fetch_run", 30, 86400)
extraction_run_limit = rate_limit("extraction_run", 30, 86400)
experience_review_write_limit = rate_limit("experience_review_write", 80, 86400)
