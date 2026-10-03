"""Real PostgreSQL, explicitly offline three-job Replay/fixture/demo smoke."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from sqlalchemy.engine import make_url
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from test_m4_interview import run_with_database
from test_m6_job_matching import replay_e2e

if __name__ == "__main__":
    if not (make_url(settings.database_url).database or "").startswith("interview_agent_m6_"):
        raise SystemExit("Use an explicitly isolated interview_agent_m6_ smoke/test database")
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must never access external network")):
        result = asyncio.run(run_with_database(settings.database_url, replay_e2e))
    print(json.dumps(result, ensure_ascii=False, indent=2))
