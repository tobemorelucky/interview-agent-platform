"""R122 B/C Live API runtime: REAL governed ModelScope router, real DB.

Unlike r62/r64 this runtime does NOT block outbound HTTP and does NOT swap in
Replay fixtures. It is the plain interview_api.main:app pointed at the isolated
showcase sandbox, with an HTTP audit middleware so every browser action can be
reconciled against model_admissions rows. Only the database target is guarded.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.core.config import settings  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
if SANDBOX not in settings.database_url:
    raise SystemExit("REFUSED: R122 Live runtime must point at the showcase sandbox")

LOG = REPO / "data/r70_r122_live_ui/api_requests.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)


def guard_routes():
    from interview_api.infrastructure.models.router import build_interview_router
    router = build_interview_router()
    for route in ("interview_main", "answer_evaluation"):
        p = router.primary(route)
        if p.provider_name != "modelscope" or p.model_name != settings.modelscope_model:
            raise SystemExit(f"REFUSED route {route}: {p.provider_name}/{p.model_name}")
        if not getattr(p, "governed", False):
            raise SystemExit(f"REFUSED ungoverned route {route}")
    if router.fallback_routes:
        raise SystemExit("REFUSED: fallback routes present")
    from interview_api.modules.interview_report.agent import harness_enabled
    if harness_enabled():
        raise SystemExit("REFUSED: Coach Harness must stay OFF")


if __name__ == "__main__":
    guard_routes()
    import uvicorn
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

    print("R122 LIVE / REAL governed model / showcase sandbox; every HTTP audited", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
