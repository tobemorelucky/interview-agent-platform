"""R132 live acceptance driver (service layer, real model calls)."""
import argparse, asyncio, json, os, sys, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R132_DATABASE_URL"):
    raise SystemExit("set R132_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R132_DATABASE_URL"]

import interview_api.modules.models  # noqa: F401  (register all FKs)
import interview_api.modules.job_target.models  # noqa: F401
import interview_api.modules.purge.models  # noqa: F401
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest

ANSWERS = {
    "intro": "我是做后端和 AI 应用开发的，最近一年主要做 Agent 面试平台和金融财报 RAG 问答。Agent 平台里我负责主面试官的状态机和评价链路，RAG 项目里负责混合检索、重排和引用校验。",
    "java": "这个秒杀场景里，库存扣减先走 Redis 的 Lua 脚本做原子校验，判断库存和一人一单的 Set，扣减成功后再异步落 MySQL 订单。为了防止超卖，库存 key 用 Hash 存总量和已售，Lua 里先判断再 decrement；数据库侧再用唯一索引和乐观锁兜底。失败会回滚 Redis 并记录补偿任务。",
    "rag": "我们的 RAG 是 BGE-M3 向量召回加 BM25 的关键词召回，两路各取 50 条，用 RRF 按排名融合，再截断到 30 条交给 Jina reranker。生成前会把证据按文档和段落拼进 prompt，要求答案带引用；引用不存在的 evidence id 会被后端校验拒绝。评估上离线看 Recall@K 和引用准确率，线上看采纳率和无证据回答率。",
    "agent": "Harness 这边我把执行拆成规划、工具调用、结果校验和状态持久化四段。每个工具调用都有白名单和参数校验，执行结果统一转成 evidence，再由 verifier 检查答案是否引用了真实证据。多 Agent 并行改同一个文件时，我用文件级租约加版本号做冲突检测，冲突就回滚重跑。评估上用任务成功率、修复正确率和平均重试次数三个指标。",
}
CONFIG = {
    "java": dict(company_id=18, job_id=46, skill="java_backend"),
    "rag": dict(company_id=8, job_id=34, skill="rag_retrieval"),
    "agent": dict(company_id=6, job_id=31, skill="ai_agent_application"),
}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["java", "rag", "agent"])
    p.add_argument("--turns", type=int, default=1)
    p.add_argument("--harness", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--opening", default="INTRODUCTION")
    args = p.parse_args()
    cfg = CONFIG[args.mode]
    svc = MockInterviewService(async_session_factory)
    req = CreateSessionRequest(company_id=cfg["company_id"], job_id=cfg["job_id"],
        question_count=max(3, args.turns), interviewer_skill_id=cfg["skill"],
        adaptive_interview=True, main_harness=args.harness,
        interviewer_selection_source="USER_SELECTED")
    created = await svc.create(21, req)
    sid = created["id"] if isinstance(created, dict) else created.id
    await svc.start(sid, 21, opening=args.opening)
    detail = await svc.detail(sid, 21)
    print("SESSION", sid, "skill", cfg["skill"], "harness", args.harness)
    print("DETAIL_KEYS", sorted(detail.keys()))
    current = detail.get("current_question") or {}
    qid = current.get("question_id") or detail.get("active_question_id")
    if not qid:
        questions = detail.get("questions") or []
        qid = questions[-1]["question_id"] if questions else None
    if args.opening == "INTRODUCTION":
        await svc.answer(sid, 21, qid, str(uuid.uuid4()), ANSWERS["intro"])
    answered = 0
    for _ in range(args.turns):
        detail = await svc.detail(sid, 21)
        current = detail.get("current_question") or {}
        qid = current.get("question_id") or detail.get("active_question_id")
        if not qid:
            print("NO_ACTIVE_QUESTION", json.dumps(detail.get("completion"), ensure_ascii=False))
            break
        text = ANSWERS["java" if args.mode == "java" else "rag" if args.mode == "rag" else "agent"]
        await svc.answer(sid, 21, qid, str(uuid.uuid4()), text)
        answered += 1
        detail = await svc.detail(sid, 21)
        print("ANSWERED", answered, "next", (detail.get("current_question") or {}).get("question_id"),
              "status", detail.get("status"))
        if detail.get("status") == "COMPLETED":
            break
    await svc.end(sid, 21)
    if args.report:
        from interview_api.modules.interview_report.service import InterviewReportService
        report = await InterviewReportService(async_session_factory).generate(sid, 21)
        print("REPORT", json.dumps(report, ensure_ascii=False, default=str)[:600])
    print("DONE", sid)

asyncio.run(main())
