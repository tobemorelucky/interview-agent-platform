"""R121 stage 2: fixture-only API runtime for browser UI acceptance.

The real FastAPI app, real routers and real PostgreSQL services are used. Only
the model providers are swapped for explicit Replay fixtures; every outbound
HTTP request is refused at the client seam. Every API response is logged to a
JSONL file so the browser actions can be reconciled against database rows.

Never use this runtime for a Live acceptance run.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

from interview_api.core.config import settings  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
if SANDBOX not in settings.database_url:
    raise SystemExit("REFUSED: UI runtime must point at the isolated showcase sandbox")
LOG = REPO / "data/r64_showcase_ui/api_requests.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)


def _forbidden(*args, **kwargs):
    raise AssertionError("R121 UI REPLAY: outbound HTTP is forbidden")


if __name__ == "__main__":
    import uvicorn
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.router import (
        INTERVIEW_COACH_ROUTE, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, ModelRouter,
    )
    from interview_api.modules.interview import router as interview_router
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview_report import router as report_router
    from interview_api.modules.interview_report.service import InterviewReportService
    from v21_support import AdaptiveReplayProvider
    from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
    from test_coach_harness import response_for

    class UICoachProvider(ModelProvider):
        """Fixture that cites the real persisted evaluations, so a legal memory
        proposal exists for the UI to approve. Zero network, zero tokens."""
        provider_name, model_name = "replay", "DEMO_UI_COACH_FIXTURE"

        async def generate(self, messages, *, schema):
            import json
            payload = json.loads(messages[-1]["content"])["input"]
            ids = [e["id"] for e in (payload.get("evaluations") or [])]
            return ModelResponse(json.dumps(response_for(ids), ensure_ascii=False),
                                 {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    def build_interview():
        provider = AdaptiveReplayProvider()
        return MockInterviewService(async_session_factory, ModelRouter(
            {INTERVIEW_MAIN_ROUTE: provider, ANSWER_EVALUATION_ROUTE: provider}))

    def build_report():
        return InterviewReportService(async_session_factory, ModelRouter(
            {INTERVIEW_COACH_ROUTE: UICoachProvider()}))

    interview_router.build_core = build_interview
    report_router.build_core = build_report

    from interview_api.main import app

    @app.middleware("http")
    async def audit_requests(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "method": request.method,
                 "path": request.url.path, "status": response.status_code,
                 "ms": round((time.perf_counter() - started) * 1000, 1)}
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return response

    print("R121 UI / REPLAY / outbound HTTP blocked; NOT a Live acceptance", flush=True)
    with patch("httpx.AsyncClient.request", side_effect=_forbidden):
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
