"""Runtime path bootstrap for local worker development.

The worker depends on the API package for shared settings, repositories, and
resume processing code. In local development the API package may be installed
into the worker virtualenv as a stale wheel, so the worker must prefer the
repo's live `apps/api/src` tree.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_api_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    api_src = repo_root / "apps" / "api" / "src"
    api_src_text = str(api_src)
    if api_src.exists() and api_src_text not in sys.path:
        sys.path.insert(0, api_src_text)


ensure_api_src_on_path()
