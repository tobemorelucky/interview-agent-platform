"""Explicit administrator/operator entry point; no new user-facing API/UI."""

import argparse
import asyncio
import json

import interview_api.modules.models  # noqa: F401
from interview_api.infrastructure.db.engine import engine
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent


async def run(task_id: int, force: bool) -> int:
    try:
        result = await PublicDataResearchAgent(async_session_factory).run(task_id, force=force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["status"] == "FAILED" else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract evidence-backed public data from a fetched refresh task")
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--force", action="store_true", help="Explicitly rerun unapproved interview drafts only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.task_id, args.force)))
