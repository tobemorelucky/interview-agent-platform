"""Redis-backed rate limiting dependencies."""

import logging
import time
import uuid
from collections.abc import Callable

import redis.asyncio as redis
from fastapi import Depends, Request

from interview_api.api.deps import get_current_user
from interview_api.core.config import settings
from interview_api.core.errors import RateLimitExceededError
from interview_api.modules.users.models import User

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


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
        raise


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
interview_chat_limit = rate_limit("interview_chat", 20, 60)
memory_write_limit = rate_limit("memory_write", 30, 60)
experience_task_limit = rate_limit("experience_task", 20, 86400)
search_run_limit = rate_limit("search_run", 30, 86400)
