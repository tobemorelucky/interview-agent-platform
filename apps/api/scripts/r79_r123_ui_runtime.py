"""R123 stage 4: browser-acceptance API runtime for the 50-JD showcase DB.

Real FastAPI/routers/DB, Replay fixtures at the model seam, outbound HTTP
blocked. 0 model admissions. Verifies the UI can list/select all 50 curated
jobs, carry different JD requirements and create+answer interviews.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

from interview_api.core.config import settings  # noqa: E402

SANDBOX = "interview_agent_showcase_r123_20261117"
if SANDBOX not in settings.database_url:
    raise SystemExit("REFUSED: R123 UI runtime must point at the R123 showcase DB")
LOG = REPO / "data/r79_r123_ui/api_requests.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)


def _forbidden(*args, **kwargs):
    raise AssertionError("R123 UI REPLAY: outbound HTTP forbidden")


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

    def build_interview():
        p = AdaptiveReplayProvider()
        return MockInterviewService(async_session_factory, ModelRouter(
            {INTERVIEW_MAIN_ROUTE: p, ANSWER_EVALUATION_ROUTE: p}))

    def build_report():
        return InterviewReportService(async_session_factory, ModelRouter(
            {INTERVIEW_COACH_ROUTE: AdaptiveReplayProvider()}))

    interview_router.build_core = build_interview
    report_router.build_core = build_report

    from interview_api.main import app

    @app.middleware("http")
    async def audit_requests(request, call_next):
        started = time.perf_counter()
        response_ = await call_next(request)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "method": request.method,
                 "path": request.url.path, "status": response_.status_code,
                 "ms": round((time.perf_counter() - started) * 1000, 1)}
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return response_

    print("R123 UI / REPLAY / outbound HTTP blocked; 50 curated jobs; NOT a Live run", flush=True)
    with __import__("unittest.mock", fromlist=["patch"]).patch("httpx.AsyncClient.request", side_effect=_forbidden):
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
