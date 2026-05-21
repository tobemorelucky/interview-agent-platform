"""Safe asyncio runner for Celery tasks — avoids "Event loop is closed" on repeat calls."""

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine with a fresh event loop each time.

    Unlike ``asyncio.run()``, this works reliably inside a long-running
    Celery worker process that may invoke async tasks repeatedly.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
