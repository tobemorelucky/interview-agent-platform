from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.getenv("M1_TEST_DATABASE_URL")
    if not value:
        pytest.skip("M1_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    return value

