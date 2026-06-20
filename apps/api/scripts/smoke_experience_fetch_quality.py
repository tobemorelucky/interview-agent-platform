"""Smoke checks for Phase 4 Step 6.5 fetch quality APIs."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select

from interview_api.core.locks import acquire_lock, release_lock
from interview_api.core.rate_limit import get_redis_client
from interview_api.core.security import create_access_token, hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.main import app
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.experience import service as experience_service
from interview_api.modules.experience.fetchers.base import FetchResult
from interview_api.modules.experience.models import (
    ExperienceCollectionTask,
    ExperienceSourceItem,
)
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url
from interview_api.modules.users.models import User


LONG_TEXT = "这是用于抓取质量 smoke 的正文。" * 120
SHORT_TEXT = "短正文" * 40


class FakeContentFetcher:
    async def fetch(self, url: str) -> FetchResult:
        if "success" in url:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                title="smoke fetched title",
                raw_text=LONG_TEXT,
            )
        if "short" in url:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                title="smoke short title",
                raw_text=SHORT_TEXT,
            )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=403,
            content_type="text/html",
            title=None,
            raw_text=None,
            error_message="http_403",
        )


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str) -> None:
    print(f"[OK] {message}")


def make_token(user_id: int, role: str) -> str:
    return create_access_token(user_id, role)


async def ensure_redis() -> None:
    await get_redis_client().ping()


async def create_user(email_prefix: str, marker: str, role: str) -> User:
    user = User(
        email=f"{email_prefix}-{marker}@example.test",
        username=None,
        password_hash=hash_password("123456"),
        role=role,
        is_active=True,
    )
    return user


async def add_source(
    task_id: int,
    url: str,
    *,
    status: str = "DISCOVERED",
    raw_text: str | None = None,
    error_message: str | None = None,
    platform: str | None = None,
) -> ExperienceSourceItem:
    normalized = normalize_url(url) or url
    return ExperienceSourceItem(
        task_id=task_id,
        source_url=url,
        normalized_url_hash=hash_url(normalized),
        platform=platform,
        title=f"source {url.rsplit('/', 1)[-1]}",
        snippet="smoke snippet",
        query_text="smoke query",
        engine="fake",
        matched_reason="smoke",
        fetch_status=status,
        raw_text=raw_text,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        if raw_text
        else None,
        fetched_at=datetime.now(timezone.utc) if status != "DISCOVERED" else None,
        error_message=error_message,
    )


async def create_smoke_data() -> dict:
    marker = str(uuid.uuid4())
    async with async_session_factory() as db:
        admin = await create_user("fetch-quality-admin", marker, "ADMIN")
        normal_user = await create_user("fetch-quality-user", marker, "USER")
        db.add_all([admin, normal_user])
        await db.flush()

        task = ExperienceCollectionTask(
            created_by=admin.id,
            search_scope="JOB",
            time_window_hours=24,
            job_keywords_json=["smoke"],
            company_keywords_json=[],
            platforms_json=[],
            max_results=10,
            review_mode="MANUAL",
            found_url_count=6,
            fetched_count=2,
            failed_count=3,
            status="FETCH_COMPLETED",
            progress=100,
        )
        db.add(task)
        await db.flush()

        sources = [
            await add_source(
                task.id,
                f"https://example.test/{marker}/fetched-good",
                status="FETCHED",
                raw_text=LONG_TEXT,
                platform="牛客",
            ),
            await add_source(
                task.id,
                f"https://example.test/{marker}/fetched-short",
                status="FETCHED",
                raw_text=SHORT_TEXT,
            ),
            await add_source(
                task.id,
                f"https://example.test/{marker}/failed-403",
                status="FETCH_FAILED",
                error_message="http_403",
                platform="牛客",
            ),
            await add_source(
                task.id,
                f"https://example.test/{marker}/failed-timeout",
                status="FETCH_FAILED",
                error_message="timeout",
            ),
            await add_source(
                task.id,
                f"https://example.test/{marker}/failed-success",
                status="FETCH_FAILED",
                error_message="timeout",
            ),
            await add_source(
                task.id,
                f"https://example.test/{marker}/pending-success",
                status="DISCOVERED",
            ),
        ]
        db.add_all(sources)
        await db.commit()

        return {
            "marker": marker,
            "admin_id": admin.id,
            "user_id": normal_user.id,
            "task_id": task.id,
            "source_ids": {source.source_url.rsplit("/", 1)[-1]: source.id for source in sources},
        }


async def cleanup_smoke_data(data: dict) -> None:
    marker = data["marker"]
    async with async_session_factory() as db:
        await db.execute(
            delete(ExperienceCollectionTask).where(
                ExperienceCollectionTask.id == data["task_id"]
            )
        )
        await db.execute(delete(User).where(User.id.in_([data["admin_id"], data["user_id"]])))
        await db.execute(
            delete(AuditLog).where(AuditLog.request_id.like(f"fetch-quality-{marker}%"))
        )
        await db.commit()


async def admin_client(data: dict) -> httpx.AsyncClient:
    token = make_token(data["admin_id"], "ADMIN")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def user_client(data: dict) -> httpx.AsyncClient:
    token = make_token(data["user_id"], "USER")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def check_stats_and_source_list(data: dict) -> None:
    marker = data["marker"]
    async with await admin_client(data) as client:
        stats_response = await client.get(
            f"/api/v1/admin/experience/tasks/{data['task_id']}/fetch-stats",
            headers={"X-Request-ID": f"fetch-quality-{marker}-stats"},
        )
        assert_true(stats_response.status_code == 200, stats_response.text)
        stats = stats_response.json()["data"]

        assert_true(stats["total"] == 6, f"total mismatch: {stats}")
        assert_true(stats["fetched_count"] == 2, f"fetched mismatch: {stats}")
        assert_true(stats["failed_count"] == 3, f"failed mismatch: {stats}")
        assert_true(stats["pending_count"] == 1, f"pending mismatch: {stats}")
        assert_true(stats["avg_raw_text_chars"] > 0, "avg chars should be positive")
        reasons = {item["reason"]: item["count"] for item in stats["failure_reasons"]}
        assert_true(reasons.get("http_403") == 1, f"failure reasons mismatch: {reasons}")
        assert_true(reasons.get("timeout") == 2, f"failure reasons mismatch: {reasons}")

        source_response = await client.get(
            f"/api/v1/admin/experience/tasks/{data['task_id']}/sources",
            headers={"X-Request-ID": f"fetch-quality-{marker}-sources"},
        )
        assert_true(source_response.status_code == 200, source_response.text)
        items = source_response.json()["data"]["items"]
        by_quality = {item["source_url"].rsplit("/", 1)[-1]: item for item in items}
        assert_true(by_quality["fetched-good"]["raw_text_char_count"] == len(LONG_TEXT), "raw_text length mismatch")
        assert_true(by_quality["fetched-good"]["fetch_quality"] == "GOOD", "GOOD quality mismatch")
        assert_true(by_quality["fetched-short"]["fetch_quality"] == "SHORT", "SHORT quality mismatch")
        assert_true(by_quality["failed-403"]["fetch_quality"] == "FAILED", "FAILED quality mismatch")
        assert_true(by_quality["pending-success"]["fetch_quality"] == "PENDING", "PENDING quality mismatch")

    print_ok("fetch stats, failure reasons, platform stats and source quality fields")


async def check_preview_and_permissions(data: dict) -> None:
    marker = data["marker"]
    good_id = data["source_ids"]["fetched-good"]
    async with await admin_client(data) as client:
        response = await client.get(
            f"/api/v1/admin/experience/sources/{good_id}/preview",
            headers={"X-Request-ID": f"fetch-quality-{marker}-preview"},
        )
        assert_true(response.status_code == 200, response.text)
        preview = response.json()["data"]
        assert_true(preview["raw_text_char_count"] == len(LONG_TEXT), "preview char count mismatch")
        assert_true(len(preview["raw_text_preview"]) <= 2000, "preview should be capped at 2000 chars")

    async with await user_client(data) as client:
        stats_response = await client.get(
            f"/api/v1/admin/experience/tasks/{data['task_id']}/fetch-stats",
            headers={"X-Request-ID": f"fetch-quality-{marker}-user-stats"},
        )
        preview_response = await client.get(
            f"/api/v1/admin/experience/sources/{good_id}/preview",
            headers={"X-Request-ID": f"fetch-quality-{marker}-user-preview"},
        )
        assert_true(stats_response.status_code == 403, f"normal user stats status: {stats_response.text}")
        assert_true(preview_response.status_code == 403, f"normal user preview status: {preview_response.text}")

    print_ok("preview cap and admin-only permission checks")


async def check_single_source_retry_and_lock(data: dict) -> None:
    marker = data["marker"]
    source_id = data["source_ids"]["failed-success"]
    lock_key = f"experience:source:{source_id}:fetch"
    token = await acquire_lock(lock_key, 30)
    assert_true(token is not None, "pre-acquiring source fetch lock failed")
    try:
        async with await admin_client(data) as client:
            locked = await client.post(
                f"/api/v1/admin/experience/sources/{source_id}/fetch",
                json={"force": True},
                headers={"X-Request-ID": f"fetch-quality-{marker}-source-locked"},
            )
            assert_true(locked.status_code == 409, f"source lock should return 409: {locked.text}")
    finally:
        await release_lock(lock_key, token)

    async with await admin_client(data) as client:
        response = await client.post(
            f"/api/v1/admin/experience/sources/{source_id}/fetch",
            json={"force": True},
            headers={"X-Request-ID": f"fetch-quality-{marker}-source-fetch"},
        )
        assert_true(response.status_code == 200, response.text)
        payload = response.json()["data"]
        assert_true(payload["fetch_status"] == "FETCHED", f"expected fake success: {payload}")
        assert_true(payload["raw_text_char_count"] == len(LONG_TEXT), "single-source retry char count mismatch")

    async with async_session_factory() as db:
        audit_logs = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "experience.source.fetch",
                    AuditLog.request_id.like(f"fetch-quality-{marker}-source%"),
                )
            )
        ).scalars().all()
        assert_true(len(audit_logs) >= 2, "expected source fetch audit logs")

    print_ok("single-source retry is locked and audited")


async def check_retry_failed_only(data: dict) -> None:
    marker = data["marker"]
    pending_id = data["source_ids"]["pending-success"]
    failed_id = data["source_ids"]["failed-timeout"]

    async with await admin_client(data) as client:
        response = await client.post(
            f"/api/v1/admin/experience/tasks/{data['task_id']}/fetch",
            json={"retry_failed": True, "limit": 20},
            headers={"X-Request-ID": f"fetch-quality-{marker}-retry-failed"},
        )
        assert_true(response.status_code == 200, response.text)
        result = response.json()["data"]
        assert_true(result["total"] >= 1, f"expected failed rows retried: {result}")

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ExperienceSourceItem).where(
                    ExperienceSourceItem.id.in_([pending_id, failed_id])
                )
            )
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        assert_true(by_id[pending_id].fetch_status == "DISCOVERED", "retry_failed should not fetch DISCOVERED rows")
        assert_true(by_id[failed_id].fetch_status == "FETCH_FAILED", "fake failed URL should remain FETCH_FAILED")

    print_ok("retry_failed=true only retries FETCH_FAILED rows")


async def main() -> None:
    await ensure_redis()
    original_fetcher = experience_service.HttpxContentFetcher
    experience_service.HttpxContentFetcher = FakeContentFetcher
    data = await create_smoke_data()
    try:
        await check_stats_and_source_list(data)
        await check_preview_and_permissions(data)
        await check_single_source_retry_and_lock(data)
        await check_retry_failed_only(data)
        print("[OK] experience fetch quality smoke completed")
    finally:
        experience_service.HttpxContentFetcher = original_fetcher
        await cleanup_smoke_data(data)


if __name__ == "__main__":
    asyncio.run(main())
