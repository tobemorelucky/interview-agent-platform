"""Answer the active question of an existing acceptance session, then end (optional report)."""
import argparse, asyncio, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R132_DATABASE_URL"):
    raise SystemExit("set R132_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R132_DATABASE_URL"]
import interview_api.modules.models, interview_api.modules.job_target.models, interview_api.modules.purge.models  # noqa
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview_report.service import InterviewReportService

TEXT = ("Harness 这边我把执行拆成规划、工具调用、结果校验和状态持久化四段。"
        "每个工具调用都有白名单和参数校验，执行结果统一转成 evidence，再由 verifier 检查答案是否引用了真实证据。"
        "多 Agent 并行改同一个文件时，我用文件级租约加版本号做冲突检测，冲突就回滚重跑。"
        "评估上看任务成功率、修复正确率和平均重试次数。")

async def main():
    p=argparse.ArgumentParser(); p.add_argument("--session",type=int,required=True); p.add_argument("--report",action="store_true")
    a=p.parse_args(); svc=MockInterviewService(async_session_factory)
    d=await svc.detail(a.session,21); qid=d.get("active_question_id")
    print("ACTIVE",qid,"STATUS",d.get("status"),"QUESTIONS",len(d.get("questions") or []))
    if qid:
        await svc.answer(a.session,21,qid,str(uuid.uuid4()),TEXT)
        d=await svc.detail(a.session,21)
        print("AFTER",d.get("status"),"QUESTIONS",len(d.get("questions") or []))
    await svc.end(a.session,21)
    if a.report:
        r=await InterviewReportService(async_session_factory).generate(a.session,21)
        print("REPORT",str(r)[:500])

asyncio.run(main())
