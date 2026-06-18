"""HTTP middleware for request context and request logging."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            if request.url.path not in {"/", "/api/v1/health"}:
                logger.info(
                    "[request] id=%s method=%s path=%s status=%s duration_ms=%s",
                    request_id,
                    request.method,
                    request.url.path,
                    status_code,
                    duration_ms,
                )
            try:
                response.headers["X-Request-ID"] = request_id
            except Exception:
                pass
