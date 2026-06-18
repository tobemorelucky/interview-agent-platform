"""Smoke checks for Phase 4 Step 6 experience source fetching."""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
from sqlalchemy import delete, select

from interview_api.core.locks import acquire_lock, release_lock
from interview_api.core.rate_limit import get_redis_client
from interview_api.core.security import create_access_token, hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.main import app
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.experience.models import (
    ExperienceCollectionTask,
    ExperienceSourceItem,
)
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url
from interview_api.modules.users.models import User

HTML_URL = os.getenv(
    "EXPERIENCE_FETCH_SMOKE_HTML_URL",
    "https://www.rfc-editor.org/rfc/rfc9110.html",
)
NON_HTML_URL = os.getenv(
    "EXPERIENCE_FETCH_SMOKE_NON_HTML_URL",
    "https://www.rfc-editor.org/rfc/rfc9110.txt",
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str) -> None:
    print(f"[OK] {message}")


async def ensure_redis() -> None:
    await get_redis_client().ping()


async def create_smoke_data() -> tuple[int, int, str]:
    marker = str(uuid.uuid4())
    async with async_session_factory() as db:
        admin = User(
            email=f"fetch-smoke-{marker}@example.test",
            username=None,
            password_hash=hash_password("123456"),
            role="ADMIN",
            is_active=True,
        )
        db.add(admin)
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
            found_url_count=4,
            status="SEARCH_COMPLETED",
            progress=100,
        )
        db.add(task)
        await db.flush()

        urls = [
            HTML_URL,
            "not-a-url",
            "http://127.0.0.1:8000/private",
            NON_HTML_URL,
        ]
        for index, url in enumerate(urls, start=1):
            normalized = normalize_url(url) or f"invalid://smoke/{marker}/{index}"
            db.add(
                ExperienceSourceItem(
                    task_id=task.id,
                    source_url=url,
                    normalized_url_hash=hash_url(normalized),
                    platform=None,
                    title=f"smoke source {index}",
                    fetch_status="DISCOVERED",
                )
            )
        await db.commit()
        return task.id, admin.id, marker


async def cleanup_smoke_data(task_id: int, admin_id: int, marker: str) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(ExperienceCollectionTask).where(ExperienceCollectionTask.id == task_id))
        await db.execute(delete(AuditLog).where(AuditLog.request_id.like(f"fetch-smoke-{marker}%")))
        await db.execute(delete(User).where(User.id == admin_id))
        await db.commit()


async def fetch_sources_via_api(task_id: int, admin_id: int, marker: str) -> dict:
    token = create_access_token(admin_id, "ADMIN")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/admin/experience/tasks/{task_id}/fetch",
            json={"retry_failed": False, "limit": 20},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Request-ID": f"fetch-smoke-{marker}-success",
            },
            timeout=60,
        )
    assert_true(response.status_code == 200, f"fetch API failed: {response.status_code} {response.text}")
    return response.json()["data"]


async def check_duplicate_lock(task_id: int, admin_id: int, marker: str) -> None:
    token = create_access_token(admin_id, "ADMIN")
    lock_key = f"experience:task:{task_id}:fetch"
    lock_token = await acquire_lock(lock_key, 30)
    assert_true(lock_token is not None, "pre-acquiring fetch lock failed")
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/admin/experience/tasks/{task_id}/fetch",
                json={"retry_failed": False, "limit": 20},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-ID": f"fetch-smoke-{marker}-locked",
                },
                timeout=30,
            )
        assert_true(response.status_code == 409, f"duplicate fetch lock should return 409: {response.text}")
        assert_true(response.json()["error"]["code"] == "RESOURCE_LOCKED", "lock error code mismatch")
    finally:
        await release_lock(lock_key, lock_token)
    print_ok("duplicate fetch lock returns RESOURCE_LOCKED")


async def check_fetch_results(task_id: int, marker: str, result: dict) -> None:
    assert_true(result["total"] == 4, f"expected 4 fetched candidates, got {result}")
    assert_true(result["fetched_count"] >= 1, "expected at least one FETCHED source")
    assert_true(result["failed_count"] >= 3, "expected failed invalid/ssrf/non-html sources")

    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(ExperienceSourceItem).where(ExperienceSourceItem.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        by_url = {row.source_url: row for row in rows}

        html_item = by_url[HTML_URL]
        assert_true(html_item.fetch_status == "FETCHED", f"HTML URL should be FETCHED: {html_item.error_message}")
        assert_true(bool(html_item.raw_text) and len(html_item.raw_text or "") >= 200, "raw_text not written")
        assert_true(bool(html_item.content_hash) and len(html_item.content_hash or "") == 64, "content_hash missing")
        assert_true(html_item.fetched_at is not None, "fetched_at missing")

        invalid_item = by_url["not-a-url"]
        assert_true(invalid_item.fetch_status == "FETCH_FAILED", "invalid URL should fail")
        assert_true(invalid_item.error_message == "invalid_url", f"invalid URL code mismatch: {invalid_item.error_message}")

        ssrf_item = by_url["http://127.0.0.1:8000/private"]
        assert_true(ssrf_item.fetch_status == "FETCH_FAILED", "localhost URL should fail")
        assert_true(ssrf_item.error_message == "ssrf_blocked", f"SSRF code mismatch: {ssrf_item.error_message}")

        non_html_item = by_url[NON_HTML_URL]
        assert_true(non_html_item.fetch_status == "FETCH_FAILED", "non-HTML URL should fail")
        assert_true(non_html_item.error_message == "non_html_content", f"non-HTML code mismatch: {non_html_item.error_message}")

        audit_count = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "experience.task.fetch",
                    AuditLog.request_id.like(f"fetch-smoke-{marker}%"),
                )
            )
        ).scalars().all()
        assert_true(len(audit_count) >= 2, "expected success and lock audit logs")
    print_ok("fetch statuses, raw_text, hash, fetched_at and audit logs")


async def main() -> None:
    await ensure_redis()
    task_id, admin_id, marker = await create_smoke_data()
    try:
        await check_duplicate_lock(task_id, admin_id, marker)
        result = await fetch_sources_via_api(task_id, admin_id, marker)
        await check_fetch_results(task_id, marker, result)
        print("[OK] experience fetch smoke completed")
    finally:
        await cleanup_smoke_data(task_id, admin_id, marker)


if __name__ == "__main__":
    asyncio.run(main())
