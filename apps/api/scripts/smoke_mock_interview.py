"""Explicit offline Replay/fixture/demo E2E; actual PostgreSQL persistence."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from interview_api.core.config import settings
from test_m4_interview import run_with_database, full_replay


if __name__ == "__main__":
    if "m4_" not in settings.database_url:
        raise SystemExit("Use an explicitly isolated m4_ smoke/test DATABASE_URL")
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must never access network")):
        result = asyncio.run(run_with_database(settings.database_url, full_replay))
    print(json.dumps(result, ensure_ascii=False, indent=2))
