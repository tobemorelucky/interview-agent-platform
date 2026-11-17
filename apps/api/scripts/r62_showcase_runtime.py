"""SHOWCASE-R1 / frontend rehearsal API runtime.

Explicit fixture-only boundary: the API talks to the isolated showcase database
and every outbound HTTP request from the process is refused at the client seam.
The pages we verify only read already-persisted Replay sessions, so no model
route is reached at all; this patch makes an accidental call fail loudly instead
of silently spending a real ModelScope request.

Usage (from apps/api):
  DATABASE_URL=.../interview_agent_showcase_r1_20261116 \
  MODEL_LEDGER_DATABASE=interview_agent_showcase_r1_20261116 \
  python scripts/r62_showcase_runtime.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.core.config import settings  # noqa: E402

if "interview_agent_showcase_r1_20261116" not in settings.database_url:
    raise SystemExit("REFUSED: API runtime must point at the isolated showcase sandbox")


def _forbidden(*args, **kwargs):
    raise AssertionError("SHOWCASE REPLAY: outbound model/HTTP call is forbidden")


if __name__ == "__main__":
    import uvicorn
    print("SHOWCASE / REPLAY / fixture API — outbound HTTP blocked; NOT a Live model run", flush=True)
    with patch("httpx.AsyncClient.request", side_effect=_forbidden):
        uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000, log_level="warning")
