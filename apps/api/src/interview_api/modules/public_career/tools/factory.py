"""Tool definitions and provider-to-gateway adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from interview_api.core.config import settings
from interview_api.modules.public_career.tools.contracts import (
    FetchedContentPayload,
    SourceCandidatePayload,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.tools.gateway import ToolGateway
from interview_api.modules.public_career.tools.providers import (
    FetchProvider,
    SearchProvider,
)


SEARCH_TOOL_NAME = "public.search"
FETCH_TOOL_NAME = "public.fetch"


class SearchToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    time_window_hours: int = Field(gt=0)
    max_results: int = Field(ge=1, le=100)
    need_content: bool = False
    sites: list[str] = Field(default_factory=list, max_length=3)
    published_after: str | None = None
    published_before: str | None = None


class FetchToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


def build_tool_gateway(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    search_provider: SearchProvider,
    fetch_provider: FetchProvider,
) -> ToolGateway:
    gateway = ToolGateway(session_factory)

    async def search_handler(
        arguments: BaseModel, _context: ToolContext
    ) -> ToolResult:
        parsed = SearchToolInput.model_validate(arguments)
        options = getattr(search_provider, "search_with_options", None)
        results = await options(parsed.query, max_results=parsed.max_results,
            need_content=parsed.need_content, sites=parsed.sites,
            published_after=parsed.published_after, published_before=parsed.published_before) if options else await search_provider.search(
            parsed.query,
            time_window_hours=parsed.time_window_hours,
            max_results=parsed.max_results,
        )
        result_dicts = [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "engine": item.engine,
                "published_at": item.published_at,
                "metadata": item.metadata,
                "body_available": bool(item.content),
            }
            for item in results
        ]
        return ToolResult(
            data={"results": result_dicts, "source_filter": getattr(search_provider, "last_search_diagnostics", {})},
            source_candidates=[
                SourceCandidatePayload(
                    canonical_url=item.url,
                    title=item.title or None,
                    metadata={
                        "snippet": item.snippet,
                        "engine": item.engine,
                        "published_at": item.published_at,
                        **item.metadata,
                    },
                )
                for item in results
            ],
            # Only the explicit actual-body field can create a Snapshot.
            # Search snippets and provider summaries are not evidence.
            fetched_contents=[FetchedContentPayload(canonical_url=item.url, title=item.title,
                fetched_at=datetime.now(timezone.utc), content=item.content,
                metadata={**item.metadata, "body_origin": "SEARCH_CONTENT"})
                for item in results if item.content and item.content.strip()],
        )

    async def fetch_handler(
        arguments: BaseModel, _context: ToolContext
    ) -> ToolResult:
        parsed = FetchToolInput.model_validate(arguments)
        result = await fetch_provider.fetch(parsed.url)
        if not result.ok or not result.raw_text or not result.final_url:
            raise ToolExecutionError(
                result.error_code or "fetch_failed",
                result.error_message or "Public source fetch failed",
                details={
                    "url": parsed.url,
                    "status_code": result.status_code,
                    "content_type": result.content_type,
                },
            )
        return ToolResult(
            data={
                "url": result.url,
                "final_url": result.final_url,
                "title": result.title,
                "content": result.raw_text,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "fetch_method": result.fetch_method,
                "metadata": result.metadata,
            },
            fetched_contents=[
                FetchedContentPayload(
                    canonical_url=result.final_url,
                    title=result.title,
                    fetched_at=datetime.now(timezone.utc),
                    content=result.raw_text,
                    metadata={
                        "status_code": result.status_code,
                        "content_type": result.content_type,
                        "fetch_method": result.fetch_method,
                        **result.metadata,
                    },
                )
            ],
        )

    gateway.register(
        ToolDefinition(
            name=SEARCH_TOOL_NAME,
            version="1.0",
            description="Search public career data sources",
            input_model=SearchToolInput,
            timeout_seconds=max(settings.public_search_tool_timeout_seconds, 90) if options_supported(search_provider) else settings.public_search_tool_timeout_seconds,
        ),
        provider_name=search_provider.provider_name,
        provider_mode=search_provider.provider_mode,
        handler=search_handler,
    )
    gateway.register(
        ToolDefinition(
            name=FETCH_TOOL_NAME,
            version="1.0",
            description="Safely fetch public career source content",
            input_model=FetchToolInput,
            timeout_seconds=max(settings.public_fetch_tool_timeout_seconds, 90) if fetch_provider.provider_name == "deepseek_web" else settings.public_fetch_tool_timeout_seconds,
        ),
        provider_name=fetch_provider.provider_name,
        provider_mode=fetch_provider.provider_mode,
        handler=fetch_handler,
    )
    return gateway


def options_supported(provider):
    return callable(getattr(provider, "search_with_options", None))


def build_live_tool_gateway(
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolGateway:
    """Default LIVE acquisition uses official Web API with durable HTTP caps."""
    class TaskBoundWebGateway(ToolGateway):
        async def invoke(self, tool_name, arguments, context):
            from interview_api.modules.public_career.web_collection import build_web_gateway
            return await build_web_gateway(self.session_factory, context.refresh_task_id).invoke(tool_name, arguments, context)
    return TaskBoundWebGateway(session_factory)


def build_searxng_tool_gateway(session_factory) -> ToolGateway:
    """Explicit legacy provider; never an automatic fallback for paid Web errors."""
    from interview_api.modules.experience.fetchers import HttpxContentFetcher
    from interview_api.modules.experience.search import SearxngSearchProvider

    return build_tool_gateway(
        session_factory,
        search_provider=SearxngSearchProvider(),
        fetch_provider=HttpxContentFetcher(),
    )
