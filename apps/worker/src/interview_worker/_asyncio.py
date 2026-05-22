"""Asyncio helpers for Celery tasks."""

import asyncio
from collections.abc import Coroutine
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the worker process event loop, creating it on first use."""
    global _loop

    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on the worker process event loop.

    SQLAlchemy's asyncpg connections are tied to the loop where they were
    created. Reusing one loop avoids handing pooled DB connections to a closed
    loop on the next Celery task.
    """
    return _get_loop().run_until_complete(coro)


def shutdown_loop() -> None:
    """Close the worker event loop during process shutdown."""
    global _loop

    if _loop is None or _loop.is_closed():
        return

    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None
