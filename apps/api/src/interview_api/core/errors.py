"""Application-level error types and response helpers."""

from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "无权访问该资源"):
        super().__init__("PERMISSION_DENIED", message, 403)


class RateLimitExceededError(AppError):
    def __init__(self, message: str = "请求过于频繁，请稍后再试"):
        super().__init__("RATE_LIMIT_EXCEEDED", message, 429)


class ResourceLockedError(AppError):
    def __init__(self, message: str = "资源正在处理中，请稍后再试"):
        super().__init__("RESOURCE_LOCKED", message, 409)


class ValidationAppError(AppError):
    def __init__(self, message: str = "请求参数无效", details: dict[str, Any] | None = None):
        super().__init__("VALIDATION_ERROR", message, 422, details)


class NotFoundAppError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__("NOT_FOUND", message, 404)


def error_response(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }
