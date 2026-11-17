"""R122 stage 2 prep: synthetic Java-backend user B + resume + REAL profile extraction.

Creates a clearly-labelled synthetic test user and resume in the showcase
sandbox, then runs the ONE governed resume_profile model call. Prints the ids
for the browser-driven Live run. Idempotent by a fixed email.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.core.security import hash_password  # noqa: E402
from interview_api.infrastructure.db.session import async_session_factory  # noqa: E402
from interview_api.infrastructure.models.governance_models import ModelAdmission  # noqa: E402
from interview_api.infrastructure.models.router import build_resume_profile_router  # noqa: E402
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent  # noqa: E402
from interview_api.modules.resume.models import Resume, ResumeReport  # noqa: E402
from interview_api.modules.resume.parser import ResumeParser  # noqa: E402
from interview_api.modules.users.models import User  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
EMAIL = "showcase-r2-live-B-synthetic@example.com"
PASSWORD = "ShowcaseR2LiveBOnly!"
DSN = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r71_r122_prep_b"

# Clearly synthetic Java-backend resume. No real person, no real employer data.
RAW = """SYNTHETIC TEST MATERIAL - NOT A REAL CANDIDATE
姓名：测试用户B（合成）
邮箱：showcase-r2-live-B-synthetic@example.com

教育背景：
2024-2027 某大学（合成） 软件工程 本科

技术经历（合成）：
- 使用 Java / Spring Boot 开发过订单与库存微服务，负责接口设计与单元测试。
- 使用 MySQL 设计过订单表与索引，处理过分库分表与慢查询优化。
- 使用 Redis 做热点数据缓存与分布式锁，遇到并解决过缓存穿透、击穿与一致性问题。
- 使用 Kafka 做异步解耦与订单事件通知，关注过消息丢失、重复消费与顺序性。
- 熟悉 Docker 部署与基本的 Kubernetes 概念。
- 了解 JVM、GC 调优与并发编程基础。

项目（合成）：
- 秒杀系统：用 Redis 预减库存 + Lua 脚本保证原子性，MQ 异步下单，最终一致性对账。
- 数据同步平台：基于 Canal 监听 binlog 同步到 Elasticsearch，处理过乱序与幂等。

说明：本简历为展示环境合成测试材料，不对应任何真实求职者。
"""


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.call_key, ModelAdmission.route,
                                        ModelAdmission.status))).all()
    return {"http": len(rows), "keys": [r[0] for r in rows]}


def guard():
    url = os.getenv("DATABASE_URL", "")
    ledger_db = os.getenv("MODEL_LEDGER_DATABASE", "")
    if not url.endswith("/" + SANDBOX) or ledger_db != SANDBOX:
        raise SystemExit(f"REFUSED: DATABASE_URL={url} ledger={ledger_db}")
    router = build_resume_profile_router()
    p = router.primary("resume_profile")
    if p.provider_name != "modelscope" or p.model_name != settings.modelscope_model \
            or not getattr(p, "governed", False) or router.fallback_routes:
        raise SystemExit("REFUSED: resume_profile route not governed modelscope/single")


async def main() -> int:
    guard()
    engine = create_async_engine(DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"round": "R122-B", "email": EMAIL, "synthetic": True}
    before = await ledger(factory)

    async with factory() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if user is None:
            user = User(email=EMAIL, username="SHOWCASE_R2_LIVE_B_SYNTHETIC",
                        password_hash=hash_password(PASSWORD), role="USER")
            db.add(user)
            await db.flush()
        resume = (await db.execute(select(Resume).where(Resume.user_id == user.id))).scalar_one_or_none()
        if resume is None:
            parsed = ResumeParser().parse(RAW.encode("utf-8"), "txt")
            resume = Resume(user_id=user.id, filename="SHOWCASE_R2_LIVE_B_SYNTHETIC.txt",
                            storage_key="showcase/r2-live-B/synthetic.txt", file_type="txt",
                            status="COMPLETED", raw_text=RAW)
            db.add(resume)
            await db.flush()
            db.add(ResumeReport(resume_id=resume.id, user_id=user.id,
                                summary_json={"synthetic": True, "showcase_round": "R122-B",
                                              "parser": type(parsed).__name__}))
            await db.commit()
        uid, rid = user.id, resume.id

    result["user_id"], result["resume_id"] = uid, rid
    result["password"] = PASSWORD

    after_create = await ledger(factory)
    if after_create["http"] == before["http"]:
        profile = await ResumeProfileAgent(factory, build_resume_profile_router()).run(rid)
    else:
        profile = {"status": "SKIPPED", "note": "profile already settled"}
    after = await ledger(factory)
    result["profile"] = {"status": profile.get("status"), "version_id": profile.get("version_id"),
                         "error": str(profile.get("error"))[:200] if profile.get("error") else None}
    result["profile_http_delta"] = after["http"] - before["http"]
    result["ledger"] = {"before": before["http"], "after": after["http"]}
    (OUT / "prep_b.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if profile.get("status") not in ("COMPLETED", "SKIPPED"):
        return 2
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
