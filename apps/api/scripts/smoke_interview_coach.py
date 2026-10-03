"""Explicit offline Replay/fixture/demo; real PostgreSQL two-session persistence."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from test_m4_interview import run_with_database
from test_m5_two_session_e2e import two_session_replay


if __name__ == "__main__":
    if "m5_" not in settings.database_url:
        raise SystemExit("Use an explicitly isolated m5_ smoke/test DATABASE_URL")
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must never access network")):
        result = asyncio.run(run_with_database(settings.database_url, two_session_replay))
    print(json.dumps(result, ensure_ascii=False, indent=2))
