"""Smoke checks for request governance, audit, rate limit, locks and URL safety."""

from __future__ import annotations

import asyncio
import uuid

import httpx
from sqlalchemy import delete

from interview_api.core.errors import AppError
from interview_api.core.locks import acquire_lock, release_lock
from interview_api.core.rate_limit import check_rate_limit, get_redis_client
from interview_api.core.security import create_access_token, hash_password
from interview_api.core.url_safety import validate_public_http_url
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.main import app
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.audit.service import AuditService
from interview_api.modules.users.models import User


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str) -> None:
    print(f"[OK] {message}")


def print_skip(message: str) -> None:
    print(f"[SKIP] {message}")


async def redis_available() -> bool:
    try:
        await get_redis_client().ping()
        return True
    except Exception:
        return False


@app.get("/__smoke_governance_app_error")
async def smoke_governance_app_error():
    raise AppError("SMOKE_ERROR", "smoke app error", status_code=418)


async def check_request_id_and_error_shape() -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health", headers={"X-Request-ID": "smoke-rid"})
        assert_true(response.status_code == 200, "health should return 200")
        assert_true(response.headers.get("X-Request-ID") == "smoke-rid", "request id header missing")

        error_response = await client.get("/__smoke_governance_app_error")
        payload = error_response.json()
        assert_true(error_response.status_code == 418, "AppError status should be preserved")
        assert_true(payload["error"]["code"] == "SMOKE_ERROR", "AppError code missing")
        assert_true(bool(payload["error"]["request_id"]), "AppError request_id missing")
    print_ok("request id middleware and AppError response shape")


async def check_audit_and_admin_permissions() -> None:
    request_id = f"smoke-{uuid.uuid4()}"
    email = f"smoke-{uuid.uuid4()}@example.test"
    async with async_session_factory() as db:
        user = User(
            email=email,
            username=None,
            password_hash=hash_password("123456"),
            role="USER",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        await AuditService(db).log_event(
            action="smoke.governance",
            request_id=request_id,
            actor_user_id=user.id,
            actor_role=user.role,
            resource_type="smoke",
            resource_id="governance",
            metadata_json={"source": "smoke_governance"},
        )

        result = await AuditService(db).list_events(request_id=request_id, offset=0, limit=10)
        assert_true(result["total"] >= 1, "audit event should be queryable")

        token = create_access_token(user.id, user.role)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/audit/logs",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert_true(response.status_code == 403, "normal user should not access admin audit")

        await db.execute(delete(AuditLog).where(AuditLog.request_id == request_id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()
    print_ok("audit write/query and admin permission guard")


async def check_rate_limit_and_locks() -> None:
    if not await redis_available():
        print_skip("Redis unavailable; rate limit and lock checks skipped")
        return

    key = f"rate:smoke:{uuid.uuid4()}"
    assert_true(await check_rate_limit(key, limit=1, window_seconds=60), "first request should pass")
    assert_true(not await check_rate_limit(key, limit=1, window_seconds=60), "second request should be limited")

    task_lock = f"task:smoke:search:{uuid.uuid4()}"
    first = await acquire_lock(task_lock, 5)
    second = await acquire_lock(task_lock, 5)
    assert_true(first is not None, "first task search lock should be acquired")
    assert_true(second is None, "duplicate task search lock should be blocked")
    await release_lock(task_lock, first)

    memory_lock = f"interview:smoke:memory_consolidate:{uuid.uuid4()}"
    first = await acquire_lock(memory_lock, 5)
    second = await acquire_lock(memory_lock, 5)
    assert_true(first is not None, "first memory consolidate lock should be acquired")
    assert_true(second is None, "duplicate memory consolidate lock should be blocked")
    await release_lock(memory_lock, first)
    print_ok("rate limit and duplicate operation locks")


def check_url_safety() -> None:
    assert_true(
        validate_public_http_url("https://www.nowcoder.com/discuss") == "https://www.nowcoder.com/discuss",
        "nowcoder public URL should pass",
    )

    blocked = [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "file:///etc/passwd",
        "http://192.168.1.10/private",
    ]
    for url in blocked:
        try:
            validate_public_http_url(url)
            raise AssertionError(f"{url} should be blocked")
        except AppError:
            pass
    print_ok("SSRF URL safety checks")


async def main() -> None:
    await check_request_id_and_error_shape()
    await check_audit_and_admin_permissions()
    await check_rate_limit_and_locks()
    check_url_safety()
    print("[OK] governance smoke completed")


if __name__ == "__main__":
    asyncio.run(main())
