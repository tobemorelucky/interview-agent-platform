from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.modules.experience.models import ExperienceCollectionTask
from interview_api.modules.experience.service import ExperienceTaskService
from interview_api.modules.public_career.models import (
    PublicDataRefreshTask,
    Source,
    SourceSnapshot,
)
from interview_api.modules.public_career.refresh_service import (
    InvalidRefreshTaskTransition,
    PublicDataRefreshService,
    RefreshTaskStatus,
)
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.tools.factory import (
    FETCH_TOOL_NAME,
    SEARCH_TOOL_NAME,
    build_tool_gateway,
)
from interview_api.modules.public_career.tools.replay import (
    ReplayFetchProvider,
    ReplaySearchProvider,
)


async def _new_refresh_task(session_factory, suffix: str) -> int:
    async with session_factory() as session:
        task = PublicDataRefreshTask(
            target_type="TEST",
            target_ref=suffix,
            reason="M2A test",
            idempotency_key=f"m2a:{suffix}",
        )
        session.add(task)
        await session.commit()
        return task.id


async def _test_gateway(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    first_url = f"https://example.com/{suffix}/one"
    second_url = f"https://mirror.example.com/{suffix}/two"
    content = "same deterministic body " + ("career data " * 30)
    search_provider = ReplaySearchProvider(
        [{"url": first_url, "title": "Public result", "snippet": "snippet only"}]
    )
    fetch_provider = ReplayFetchProvider(
        {
            first_url: {"content": content, "title": "First"},
            second_url: {"content": content, "title": "Mirror"},
        }
    )
    gateway = build_tool_gateway(
        session_factory,
        search_provider=search_provider,
        fetch_provider=fetch_provider,
    )
    task_id = await _new_refresh_task(session_factory, suffix)
    context = ToolContext(refresh_task_id=task_id)
    try:
        search_result = await gateway.invoke(
            SEARCH_TOOL_NAME,
            {"query": "public role", "time_window_hours": 24, "max_results": 10},
            context,
        )
        assert search_result.data["source_refs"]
        async with session_factory() as session:
            source = await session.scalar(
                select(Source).where(Source.canonical_url == first_url)
            )
            assert source is not None
            assert source.metadata_json["last_search_observation"]["snippet"] == "snippet only"
            assert await session.scalar(
                select(func.count(SourceSnapshot.id)).where(
                    SourceSnapshot.source_id == source.id
                )
            ) == 0

        first = await gateway.invoke(FETCH_TOOL_NAME, {"url": first_url}, context)
        duplicate = await gateway.invoke(FETCH_TOOL_NAME, {"url": first_url}, context)
        mirror = await gateway.invoke(FETCH_TOOL_NAME, {"url": second_url}, context)
        assert first.data["source_snapshot_refs"] == duplicate.data["source_snapshot_refs"]
        assert mirror.data["source_snapshot_refs"][0]["source_id"] != first.data[
            "source_snapshot_refs"
        ][0]["source_id"]

        async with session_factory() as session:
            snapshots = list((await session.scalars(select(SourceSnapshot))).all())
            relevant_source_ids = {
                item.id
                for item in (
                    await session.scalars(
                        select(Source).where(Source.canonical_url.in_([first_url, second_url]))
                    )
                ).all()
            }
            relevant_snapshots = [
                item for item in snapshots if item.source_id in relevant_source_ids
            ]
            assert len(relevant_snapshots) == 2
            assert len({item.content_hash for item in relevant_snapshots}) == 1
            before_failure = len(relevant_snapshots)

        with pytest.raises(ToolExecutionError) as error:
            await gateway.invoke(
                FETCH_TOOL_NAME,
                {"url": f"https://example.com/{suffix}/missing"},
                context,
            )
        assert error.value.code == "replay_fixture_not_found"
        async with session_factory() as session:
            assert await session.scalar(
                select(func.count(SourceSnapshot.id)).where(
                    SourceSnapshot.source_id.in_(relevant_source_ids)
                )
            ) == before_failure
            invocations = list(
                (
                    await session.scalars(
                        select(ToolInvocation).where(
                            ToolInvocation.refresh_task_id == task_id
                        )
                    )
                ).all()
            )
            assert len(invocations) == 5
            assert any(item.status == "FAILED" for item in invocations)
            assert all(item.refresh_task_id == task_id for item in invocations)
    finally:
        await engine.dispose()


def test_gateway_audit_and_source_persistence(database_url: str) -> None:
    asyncio.run(_test_gateway(database_url))


async def _test_refresh_and_legacy_adapter(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    url = f"https://example.com/{suffix}/experience"
    search = ReplaySearchProvider(
        [
            {
                "url": url,
                "title": "Backend 面经 interview experience",
                "snippet": "backend 面试题 questions and answers",
            }
        ]
    )
    fetch = ReplayFetchProvider(
        {url: {"content": "backend interview body " + ("question answer " * 30)}}
    )
    gateway = build_tool_gateway(
        session_factory, search_provider=search, fetch_provider=fetch
    )
    try:
        async with session_factory() as session:
            public_task = PublicDataRefreshTask(
                target_type="JOB",
                target_ref=suffix,
                idempotency_key=f"refresh:{suffix}",
            )
            session.add(public_task)
            await session.commit()
            service = PublicDataRefreshService(session, gateway)
            public_task = await service.execute_to_ready(
                public_task,
                query="backend",
                time_window_hours=24,
                max_results=5,
            )
            assert public_task.status == RefreshTaskStatus.READY_FOR_EXTRACTION.value
            with pytest.raises(InvalidRefreshTaskTransition):
                await service.transition(public_task, RefreshTaskStatus.PENDING)

        async with session_factory() as session:
            legacy_service = ExperienceTaskService(session, gateway=gateway)
            created = await legacy_service.create_task(
                {
                    "search_scope": "JOB",
                    "time_window_hours": 24,
                    "job_keywords_json": ["backend"],
                    "company_keywords_json": [],
                    "platforms_json": [],
                    "max_results": 5,
                    "review_mode": "MANUAL",
                }
            )
            assert created["public_refresh_task_id"] is not None
            await session.commit()
            legacy_id = created["id"]
            bound_id = created["public_refresh_task_id"]

        async with session_factory() as session:
            legacy_service = ExperienceTaskService(session, gateway=gateway)
            search_result = await legacy_service.run_search(legacy_id)
            assert search_result["query_success_count"] >= 1
            fetch_result = await legacy_service.fetch_task_sources(legacy_id, limit=5)
            assert fetch_result["fetched_count"] == 1
            legacy = await session.get(ExperienceCollectionTask, legacy_id)
            bound = await session.get(PublicDataRefreshTask, bound_id)
            assert legacy is not None and legacy.public_refresh_task_id == bound_id
            assert bound is not None
            assert bound.status == RefreshTaskStatus.READY_FOR_EXTRACTION.value
    finally:
        await engine.dispose()


def test_refresh_state_machine_and_legacy_adapter(database_url: str) -> None:
    asyncio.run(_test_refresh_and_legacy_adapter(database_url))


async def _test_failed_refresh(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    gateway = build_tool_gateway(
        session_factory,
        search_provider=ReplaySearchProvider(
            [{"url": f"https://example.com/{suffix}/missing", "title": "Missing"}]
        ),
        fetch_provider=ReplayFetchProvider({}),
    )
    try:
        async with session_factory() as session:
            task = PublicDataRefreshTask(target_type="TEST", target_ref=suffix)
            session.add(task)
            await session.commit()
            service = PublicDataRefreshService(session, gateway)
            task = await service.execute_to_ready(
                task, query="missing", time_window_hours=24, max_results=5
            )
            assert task.status == RefreshTaskStatus.FAILED.value
            assert "all_fetches_failed" in (task.error_message or "")
    finally:
        await engine.dispose()


def test_failed_refresh_is_persisted(database_url: str) -> None:
    asyncio.run(_test_failed_refresh(database_url))
