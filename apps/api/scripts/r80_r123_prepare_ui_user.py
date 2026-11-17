"""R123 stage 4 prep: create a clean, clearly-synthetic UI user with a Replay profile.

0 model HTTP. The user has NO memory/history in the R123 DB. Used only for the
browser UI acceptance; the real user will register their own account.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.security import hash_password  # noqa: E402
from interview_api.modules.users.models import User  # noqa: E402
from interview_api.modules.resume.models import Resume, ResumeReport  # noqa: E402
from interview_api.modules.resume.parser import ResumeParser  # noqa: E402
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent  # noqa: E402
from interview_api.infrastructure.models.router import RESUME_PROFILE_ROUTE, ModelRouter  # noqa: E402
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse  # noqa: E402
from test_m3_candidate_profile import response, RAW as FIXTURE_RAW  # noqa: E402

SANDBOX = "interview_agent_showcase_r123_20261117"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
EMAIL = "r123-ui-acceptance-synthetic@example.com"
PASSWORD = "R123-ui-acceptance-only"
OUT = REPO / "data/r80_r123_ui_user"
OUT.mkdir(parents=True, exist_ok=True)

RAW = """SYNTHETIC TEST MATERIAL — NOT A REAL CANDIDATE
姓名：R123 UI 验收合成用户
教育：某大学（合成） 计算机科学与技术 本科 2027届
技术：Java / Spring Boot / Python；熟悉 MySQL、Redis、Kafka；了解 RAG、Agent 工具调用与向量检索。
项目（合成）：基于 Spring Boot 的订单系统（缓存一致性与幂等）；基于 LangGraph 的客服 Agent（RAG 检索与工具调用）。
说明：本简历为展示环境合成测试材料，不对应任何真实求职者。
"""


class ProfileReplay(ModelProvider):
    provider_name, model_name = "replay", "R123_PROFILE_REPLAY"

    async def generate(self, messages, *, schema):
        raw = json.loads(messages[-1]["content"])
        payload = raw["input"] if "input" in raw else raw
        rid = payload.get("resume_id") or 1
        return ModelResponse(json.dumps(response(rid), ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


async def main() -> int:
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if user is None:
            user = User(email=EMAIL, username="R123_UI_SYNTHETIC",
                        password_hash=hash_password(PASSWORD), role="USER")
            db.add(user)
            await db.flush()
        resume = (await db.execute(select(Resume).where(Resume.user_id == user.id))).scalar_one_or_none()
        if resume is None:
            parsed = ResumeParser().parse(FIXTURE_RAW.encode("utf-8"), "txt")
            resume = Resume(user_id=user.id, filename="R123_UI_SYNTHETIC.txt",
                            storage_key="r123/ui/synthetic.txt", file_type="txt",
                            status="COMPLETED", raw_text=FIXTURE_RAW)
            db.add(resume)
            await db.flush()
            db.add(ResumeReport(resume_id=resume.id, user_id=user.id,
                                summary_json={"synthetic": True, "round": "R123"}))
            await db.commit()
        uid, rid = user.id, resume.id
    profile = await ResumeProfileAgent(factory,
                                       ModelRouter({RESUME_PROFILE_ROUTE: ProfileReplay()})).run(rid)
    result = {"email": EMAIL, "password": PASSWORD, "user_id": uid, "resume_id": rid,
              "profile_status": profile.get("status"), "synthetic": True}
    (OUT / "user.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    await engine.dispose()
    return 0 if profile.get("status") == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
