"""Configured logical routes and process-local provider concurrency guard."""

import asyncio
from contextvars import ContextVar
from weakref import WeakKeyDictionary

from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProvider


EXTRACT_ROUTE = "public_research_extract"
QUALITY_ROUTE = "public_research_quality"
RESUME_PROFILE_ROUTE = "resume_profile"
INTERVIEW_MAIN_ROUTE = "interview_main"
ANSWER_EVALUATION_ROUTE = "answer_evaluation"
INTERVIEW_COACH_ROUTE = "interview_coach"
JOB_MATCHING_ROUTE = "job_matching"

# Guards are shared by router instances within an event loop (not across workers).
_LOOP_GUARDS = WeakKeyDictionary()


class ModelRouter:
    def __init__(self, routes: dict[str, ModelProvider], *, concurrency: int = 2,
                 fallback_routes: dict[str, ModelProvider] | None = None):
        if concurrency < 1:
            raise ValueError("provider concurrency must be positive")
        self.routes = routes
        self.concurrency = concurrency
        self.fallback_routes = fallback_routes or {}
        self._selections = ContextVar("model_router_selections", default={})

    def resolve(self, route: str) -> ModelProvider:
        if route not in self.routes:
            raise ValueError(f"Unconfigured model route: {route}")
        return self._selections.get().get(route, self.routes[route])

    def primary(self, route: str) -> ModelProvider:
        if route not in self.routes:
            raise ValueError(f"Unconfigured model route: {route}")
        return self.routes[route]

    def record_selection(self, route: str, provider: ModelProvider) -> None:
        self._selections.set({**self._selections.get(), route: provider})

    def guard(self, route: str) -> asyncio.Semaphore:
        provider = self.primary(route)
        guards = _LOOP_GUARDS.setdefault(asyncio.get_running_loop(), {})
        # Use the endpoint/provider rather than model: two routes share its limit.
        key = (provider.provider_name, getattr(provider, "base_url", ""), self.concurrency)
        return guards.setdefault(key, asyncio.Semaphore(self.concurrency))


def _configured_router(routes: dict[str, ModelProvider], *, concurrency: int) -> ModelRouter:
    fallbacks = {}
    if settings.llm_fallback_model:
        for name, provider in routes.items():
            if isinstance(provider, CompatibleModelProvider) and provider.model_name != settings.llm_fallback_model:
                fallbacks[name] = CompatibleModelProvider(base_url=provider.base_url, api_key=provider.api_key,
                    model=settings.llm_fallback_model, timeout_seconds=provider.timeout,
                    max_output_tokens=provider.max_output_tokens)
    return ModelRouter(routes, concurrency=concurrency, fallback_routes=fallbacks)


def build_model_router() -> ModelRouter:
    routes: dict[str, ModelProvider] = {}
    for name, configured_model in (
        (EXTRACT_ROUTE, settings.public_research_extract_model),
        (QUALITY_ROUTE, settings.public_research_quality_model),
    ):
        routes[name] = CompatibleModelProvider(
            base_url=settings.public_research_base_url or settings.llm_base_url,
            api_key=settings.public_research_api_key or settings.llm_api_key,
            model=configured_model or settings.llm_model,
            timeout_seconds=settings.public_research_model_timeout_seconds,
            max_output_tokens=settings.public_research_max_output_tokens,
        )
    return _configured_router(routes, concurrency=settings.public_research_provider_concurrency)


def build_resume_profile_router() -> ModelRouter:
    """Same provider/router/guard, independently configured private-data route."""
    return _configured_router({RESUME_PROFILE_ROUTE: CompatibleModelProvider(
        base_url=settings.resume_profile_base_url or settings.llm_base_url,
        api_key=settings.resume_profile_api_key or settings.llm_api_key,
        model=settings.resume_profile_model or settings.llm_model,
        timeout_seconds=settings.resume_profile_timeout_seconds,
        max_output_tokens=settings.resume_profile_max_output_tokens,
    )}, concurrency=settings.resume_profile_provider_concurrency)


def build_interview_router() -> ModelRouter:
    return _configured_router({route: CompatibleModelProvider(
        base_url=settings.interview_model_base_url or settings.llm_base_url,
        api_key=settings.interview_model_api_key or settings.llm_api_key,
        model=model or settings.llm_model,
        timeout_seconds=settings.interview_model_timeout_seconds,
        max_output_tokens=settings.interview_model_max_output_tokens,
    ) for route, model in ((INTERVIEW_MAIN_ROUTE, settings.interview_main_model),
                          (ANSWER_EVALUATION_ROUTE, settings.answer_evaluation_model))},
        concurrency=settings.interview_provider_concurrency)


def build_interview_coach_router() -> ModelRouter:
    """Reuse shared providers/guards; report failures never affect interview state."""
    return _configured_router({INTERVIEW_COACH_ROUTE: CompatibleModelProvider(
        base_url=settings.interview_coach_base_url or settings.interview_model_base_url or settings.llm_base_url,
        api_key=settings.interview_coach_api_key or settings.interview_model_api_key or settings.llm_api_key,
        model=settings.interview_coach_model or settings.llm_model,
        timeout_seconds=settings.interview_coach_timeout_seconds,
        max_output_tokens=settings.interview_coach_max_output_tokens,
    )}, concurrency=settings.interview_coach_provider_concurrency)


def build_job_matching_router() -> ModelRouter:
    return _configured_router({JOB_MATCHING_ROUTE: CompatibleModelProvider(
        base_url=settings.job_matching_base_url or settings.llm_base_url,
        api_key=settings.job_matching_api_key or settings.llm_api_key,
        model=settings.job_matching_model or settings.llm_model,
        timeout_seconds=settings.job_matching_timeout_seconds,
        max_output_tokens=settings.job_matching_max_output_tokens,
    )}, concurrency=settings.job_matching_provider_concurrency)
