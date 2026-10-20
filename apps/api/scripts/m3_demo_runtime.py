"""Explicit REPLAY/FIXTURE/DEMO runtime, never a production fallback.

Uses real API/auth/upload/MinIO/parser/Celery/database. Only model responses
are replay fixtures, restricted to tests/fixtures/m3_resume_demo.txt.
Usage: uv run python scripts/m3_demo_runtime.py api|worker
Set DATABASE_URL to a dedicated migrated smoke database first.
"""
import argparse
import json
from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT.parent / "worker" / "src"))

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, RESUME_PROFILE_ROUTE

DEMO_FILE = API_ROOT / "tests" / "fixtures" / "m3_resume_demo.txt"
QUEUE = "m3_profile_replay_fixture_demo"


class DemoReportProvider:
    async def chat(self, messages, **kwargs):
        raw = DEMO_FILE.read_text(encoding="utf-8").strip()
        if raw not in messages[0]["content"]:
            raise ValueError("REPLAY_FIXTURE_ONLY: unsupported demo material")
        return json.dumps({"mode": "replay_fixture_demo", "skills": ["Python", "Redis"], "summary": "Demo candidate"})


class DemoProfileProvider(ReplayModelProvider):
    model_name = "m3_replay_fixture_demo_v1"

    def __init__(self):
        super().__init__([])

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        rows = payload["resume_evidence"]
        if not any(row["excerpt"] == "Demo candidate — local fixture, not real personal data" for row in rows):
            raise ValueError("REPLAY_FIXTURE_ONLY: unsupported material")
        def fact(statement, label=None, **extra):
            row = next(row for row in rows if statement in row["excerpt"])
            result = dict(evidence_ids=[row["evidence_id"]], **extra)
            if label: result["label"] = label
            return result
        body = dict(
            skills=[fact("订单系统：使用 Python 和 Redis 实现缓存。", "Redis", proposed_evidence_state="DEMONSTRATED")],
            projects=[fact("订单系统：使用 Python 和 Redis 实现缓存。", "订单系统")],
            education=[fact("示例大学 计算机科学 本科", "示例大学")],
            work_experience=[fact("示例公司 Python 开发", "示例公司")],
            internship_experience=[fact("示例实验室 Redis 缓存实习", "示例实验室")],
            unknowns=["项目规模"],
        )
        self.responses.append(body)
        return await super().generate(messages, schema=schema)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["api", "worker"])
    args = parser.parse_args()
    from interview_api.core.config import settings
    if "m3_smoke" not in settings.database_url:
        raise SystemExit("Use a dedicated database with m3_smoke in its name; never normal business DB")
    from interview_api.infrastructure.tasks.celery_client import _app
    _app.conf.task_default_queue = QUEUE
    if args.role == "api":
        import uvicorn
        uvicorn.run("interview_api.main:app", host="127.0.0.1", port=8000)
    else:
        from interview_api.infrastructure.llm import provider as report_provider
        from interview_api.modules.candidate_profile import agent
        report_provider.OpenAICompatibleLLMProvider = DemoReportProvider
        agent.build_resume_profile_router = lambda: ModelRouter({RESUME_PROFILE_ROUTE: DemoProfileProvider()})
        from interview_worker.celery_app import app
        app.conf.task_default_queue = QUEUE
        app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=INFO", "-Q", QUEUE,
                         "--hostname=m3-replay-fixture-demo@%h", "--without-gossip", "--without-mingle"])


if __name__ == "__main__":
    main()
