"""Audited gateway and deterministic Source persistence for public tools."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from interview_api.core.url_safety import canonicalize_public_url
from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.models import Source, SourceSnapshot
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.tools.contracts import (
    FetchedContentPayload,
    ProviderMode,
    SourceCandidatePayload,
    ToolContext,
    ToolDefinition,
    ToolHandler,
    ToolResult,
)
from interview_api.modules.public_career.tools.errors import ToolExecutionError


_SENSITIVE_FIELD_RE = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|token)", re.IGNORECASE
)
_SENSITIVE_QUERY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    provider_name: str
    provider_mode: ProviderMode
    handler: ToolHandler


class ToolGateway:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        definition: ToolDefinition,
        *,
        provider_name: str,
        provider_mode: ProviderMode,
        handler: ToolHandler,
    ) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = RegisteredTool(
            definition=definition,
            provider_name=provider_name,
            provider_mode=provider_mode,
            handler=handler,
        )

    def definition(self, tool_name: str) -> ToolDefinition:
        registration = self._tools.get(tool_name)
        if registration is None:
            raise ToolExecutionError("tool_not_found", "Requested tool is not registered")
        return registration.definition

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        registration = self._tools.get(tool_name)
        if registration is None:
            raise ToolExecutionError("tool_not_found", "Requested tool is not registered")

        invocation_id = await self._start_invocation(registration, arguments, context)
        started = time.perf_counter()
        try:
            parsed_arguments = registration.definition.input_model.model_validate(arguments)
            result = await asyncio.wait_for(
                registration.handler(parsed_arguments, context),
                timeout=registration.definition.timeout_seconds,
            )
            latency_ms = round((time.perf_counter() - started) * 1000)
            refs = await self._complete_success(
                invocation_id, result, latency_ms=latency_ms
            )
            return result.model_copy(
                update={
                    "data": {
                        **result.data,
                        **refs,
                        "tool_invocation_id": invocation_id,
                    }
                }
            )
        except asyncio.TimeoutError as exc:
            error = ToolExecutionError("tool_timeout", "Tool execution timed out")
            await self._complete_failure(
                invocation_id,
                error,
                round((time.perf_counter() - started) * 1000),
            )
            raise error from exc
        except ValidationError as exc:
            error = ToolExecutionError(
                "invalid_tool_arguments", "Tool arguments failed validation"
            )
            await self._complete_failure(
                invocation_id,
                error,
                round((time.perf_counter() - started) * 1000),
            )
            raise error from exc
        except ToolExecutionError as exc:
            await self._complete_failure(
                invocation_id,
                exc,
                round((time.perf_counter() - started) * 1000),
            )
            raise
        except Exception as exc:
            error = ToolExecutionError(
                "tool_execution_failed", "Tool execution failed"
            )
            await self._complete_failure(
                invocation_id,
                error,
                round((time.perf_counter() - started) * 1000),
            )
            raise error from exc

    async def _start_invocation(
        self,
        registration: RegisteredTool,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> int:
        invocation = ToolInvocation(
            refresh_task_id=context.refresh_task_id,
            tool_name=registration.definition.name,
            tool_version=registration.definition.version,
            provider_name=registration.provider_name,
            provider_mode=registration.provider_mode.value,
            status="RUNNING",
            request_json=self._redact(
                arguments, registration.definition.redacted_fields
            ),
            response_json={},
            timeout_seconds=registration.definition.timeout_seconds,
            started_at=datetime.now(timezone.utc),
        )
        async with self.session_factory() as session:
            session.add(invocation)
            await session.commit()
            return invocation.id

    async def _complete_success(
        self,
        invocation_id: int,
        result: ToolResult,
        *,
        latency_ms: int,
    ) -> dict[str, list[dict[str, int]]]:
        async with self.session_factory() as session:
            invocation = await session.get(ToolInvocation, invocation_id)
            if invocation is None:
                raise RuntimeError(f"Tool invocation not found: {invocation_id}")

            source_refs: list[dict[str, int]] = []
            snapshot_refs: list[dict[str, int]] = []
            for payload in sorted(
                result.source_candidates, key=lambda item: item.canonical_url
            ):
                source_id = await self._upsert_source_candidate(session, payload)
                source_refs.append({"source_id": source_id})

            for payload in sorted(
                result.fetched_contents, key=lambda item: item.canonical_url
            ):
                source_id, snapshot_id = await self._persist_fetched_content(
                    session, payload, invocation_id=invocation_id
                )
                source_refs.append({"source_id": source_id})
                snapshot_refs.append(
                    {"source_id": source_id, "source_snapshot_id": snapshot_id}
                )

            source_refs = _dedupe_refs(source_refs, "source_id")
            response = {
                "status": result.status.value,
                "data": result.data,
                "source_refs": source_refs,
                "source_snapshot_refs": snapshot_refs,
            }
            invocation.status = "SUCCEEDED"
            invocation.response_json = response
            invocation.latency_ms = latency_ms
            invocation.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return {
                "source_refs": source_refs,
                "source_snapshot_refs": snapshot_refs,
            }

    async def _upsert_source_candidate(
        self, session: AsyncSession, payload: SourceCandidatePayload
    ) -> int:
        canonical_url = canonicalize_public_url(payload.canonical_url)
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        parsed = urlsplit(canonical_url)
        metadata = {
            "last_search_observation": {
                "title": payload.title,
                **payload.metadata,
            }
        }
        source_id = await session.scalar(
            postgres_insert(Source)
            .values(
                canonical_url=canonical_url,
                url_hash=url_hash,
                domain=parsed.hostname or "",
                source_type=payload.source_type,
                metadata_json=metadata,
            )
            .on_conflict_do_update(
                constraint="uq_sources_url_hash",
                set_={
                    "canonical_url": canonical_url,
                    "domain": parsed.hostname or "",
                    "source_type": payload.source_type,
                    "metadata_json": metadata,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(Source.id)
        )
        if source_id is None:
            raise RuntimeError("Source upsert failed")
        return source_id

    async def _persist_fetched_content(
        self,
        session: AsyncSession,
        payload: FetchedContentPayload,
        *,
        invocation_id: int,
    ) -> tuple[int, int]:
        canonical_url = canonicalize_public_url(payload.canonical_url)
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        parsed = urlsplit(canonical_url)
        source_id = await session.scalar(
            postgres_insert(Source)
            .values(
                canonical_url=canonical_url,
                url_hash=url_hash,
                domain=parsed.hostname or "",
                source_type=payload.source_type,
                metadata_json={"first_seen_via": "FETCH"},
            )
            .on_conflict_do_update(
                constraint="uq_sources_url_hash",
                set_={
                    "canonical_url": canonical_url,
                    "domain": parsed.hostname or "",
                    "source_type": payload.source_type,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(Source.id)
        )
        if source_id is None:
            raise RuntimeError("Source upsert failed")

        normalized_content = normalize_text(payload.content)
        content_hash = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
        snapshot_id = await session.scalar(
            postgres_insert(SourceSnapshot)
            .values(
                source_id=source_id,
                created_by_tool_invocation_id=invocation_id,
                content_hash=content_hash,
                fetched_at=payload.fetched_at,
                published_at=payload.published_at,
                title=payload.title,
                content_text=payload.content,
                normalized_content=normalized_content,
                metadata_json=payload.metadata,
            )
            .on_conflict_do_nothing(
                constraint="uq_source_snapshots_source_content"
            )
            .returning(SourceSnapshot.id)
        )
        if snapshot_id is None:
            snapshot_id = await session.scalar(
                select(SourceSnapshot.id).where(
                    SourceSnapshot.source_id == source_id,
                    SourceSnapshot.content_hash == content_hash,
                )
            )
        if snapshot_id is None:
            raise RuntimeError("SourceSnapshot persistence failed")
        return source_id, snapshot_id

    async def _complete_failure(
        self,
        invocation_id: int,
        error: ToolExecutionError,
        latency_ms: int,
    ) -> None:
        async with self.session_factory() as session:
            invocation = await session.get(ToolInvocation, invocation_id)
            if invocation is None:
                return
            invocation.status = "FAILED"
            invocation.error_code = error.code
            invocation.error_message = error.message[:4000]
            invocation.response_json = {"details": error.details}
            invocation.latency_ms = latency_ms
            invocation.completed_at = datetime.now(timezone.utc)
            await session.commit()

    @classmethod
    def _redact(cls, value: Any, explicit_fields: frozenset[str]) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                if key in explicit_fields or _SENSITIVE_FIELD_RE.search(key):
                    redacted[key] = "[REDACTED]"
                elif key.lower() == "url" and isinstance(item, str):
                    redacted[key] = cls._redact_url(item)
                else:
                    redacted[key] = cls._redact(item, explicit_fields)
            return redacted
        if isinstance(value, list):
            return [cls._redact(item, explicit_fields) for item in value]
        return value

    @staticmethod
    def _redact_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "[INVALID_URL]"
        query = [
            (key, "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_NAMES else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
        )


def _dedupe_refs(values: list[dict[str, int]], key: str) -> list[dict[str, int]]:
    seen: set[int] = set()
    result: list[dict[str, int]] = []
    for value in values:
        identifier = value[key]
        if identifier not in seen:
            seen.add(identifier)
            result.append(value)
    return result

