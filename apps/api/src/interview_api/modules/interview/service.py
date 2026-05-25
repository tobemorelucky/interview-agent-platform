"""Interview service: session CRUD, resume binding, chat streaming with memory."""

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.interview.models import (
    InterviewSession,
    InterviewSessionQuestion,
    InterviewMessage,
)
from interview_api.modules.interview.repository import (
    InterviewSessionRepository,
    InterviewSessionQuestionRepository,
    InterviewMessageRepository,
)
from interview_api.modules.interview.memory import InterviewMemoryManager
from interview_api.modules.interview.prompt_builder import InterviewPromptBuilder
from interview_api.modules.interview.question_retrieval import QuestionRetrievalService
from interview_api.modules.resume.repository import ResumeRepository, ResumeReportRepository

logger = logging.getLogger(__name__)


class InterviewService:
    def __init__(self, db: AsyncSession, embedding, vector_store, llm):
        self.db = db
        self.embedding = embedding
        self.vector_store = vector_store
        self.llm = llm
        self.session_repo = InterviewSessionRepository(db)
        self.msg_repo = InterviewMessageRepository(db)
        self.question_repo = InterviewSessionQuestionRepository(db)
        self.resume_repo = ResumeRepository(db)
        self.report_repo = ResumeReportRepository(db)
        self.memory_manager = InterviewMemoryManager()
        self.prompt_builder = InterviewPromptBuilder()

    # ── Session CRUD ──

    async def create_session(
        self, user_id: int, title: str | None = None
    ) -> InterviewSession:
        if title is None:
            existing = await self.session_repo.list_by_user(user_id)
            title = f"面试练习 {len(existing) + 1}"
        session = await self.session_repo.create(user_id=user_id, title=title)
        await self.db.commit()
        return session

    async def list_sessions(self, user_id: int) -> list[dict]:
        sessions = await self.session_repo.list_by_user(user_id)
        result = []
        for s in sessions:
            item = self._session_to_dict(s)
            # Attach resume filename and status if resume is bound
            if s.resume_id:
                resume = await self.resume_repo.get_by_id(s.resume_id)
                if resume:
                    item["resume_filename"] = resume.filename
                    item["resume_status"] = resume.status
            result.append(item)
        return result

    async def get_session(
        self, session_id: int, user_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        item = self._session_to_dict(session)
        if session.resume_id:
            resume = await self.resume_repo.get_by_id(session.resume_id)
            if resume:
                item["resume_filename"] = resume.filename
                item["resume_status"] = resume.status
        messages = await self.msg_repo.get_by_session_id(session_id)
        item["messages"] = [self._msg_to_dict(m) for m in messages]
        questions = await self.question_repo.get_by_session_id(session_id)
        item["questions"] = [self._question_to_dict(q) for q in questions]
        item["total_questions"] = len(questions)
        return item

    async def delete_session(self, session_id: int, user_id: int) -> bool:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return False
        await self.session_repo.delete(session_id)
        await self.db.commit()
        return True

    # ── Resume Binding ──

    async def bind_resume(
        self, session_id: int, user_id: int, resume_id: int
    ) -> None:
        """Bind an uploaded resume to an interview session.

        Raises ValueError with a user-facing message on failure.
        """
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            raise ValueError("会话不存在")

        already_bound = session.resume_id == resume_id
        if session.resume_id is not None and not already_bound:
            raise ValueError("该会话已绑定简历，每个会话仅限绑定一份简历")

        resume = await self.resume_repo.get_by_id(resume_id)
        if resume is None or resume.user_id != user_id:
            raise ValueError("简历不存在")
        if resume.status == "FAILED":
            raise ValueError("简历处理失败，请重新上传")

        if not already_bound:
            await self.session_repo.bind_resume(session_id, resume_id)

        if resume.status != "COMPLETED":
            await self.db.commit()
            return

        # Auto-suggest target position from resume
        if session.target_position is None:
            suggested = await self._suggest_position_from_resume(session_id)
            if suggested:
                await self.session_repo.update_target_position(
                    session_id,
                    target_position=suggested,
                    interview_mode="comprehensive",
                    question_count=settings.interview_question_count,
                )
                # Un-confirm so user must confirm
                from sqlalchemy import update
                await self.db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == session_id)
                    .values(target_position_confirmed=False)
                )

        # Auto-generate welcome message from assistant
        existing_messages = await self.msg_repo.get_by_session_id(session_id)
        has_welcome = any(
            m.role == "ASSISTANT" and m.turn_index == 0 for m in existing_messages
        )
        if not has_welcome:
            pos = session.target_position or "你简历匹配的岗位"
            welcome = (
                f"我已读取你的简历。根据分析，我判断你面试的目标岗位是「{pos}」，是否正确？\n\n"
                "请回复确认，或直接告诉我你实际要面试的岗位名称（如「后端开发实习」「Python 后端」「AI 应用开发」等）。"
            )
            await self.msg_repo.create(
                session_id=session_id,
                role="ASSISTANT",
                content=welcome,
                metadata_json={
                    "source": "LLM_GENERATED",
                    "type": "POSITION_SUGGESTION",
                    "suggested_position": session.target_position,
                },
                turn_index=0,
            )

        if session.title is None:
            await self.session_repo.update_title(
                session_id, f"面试练习 - {resume.filename}"
            )

        # Position not yet confirmed — generation will be triggered later.
        await self.db.commit()

    # ── Target Position ──

    async def set_target_position(
        self,
        session_id: int,
        user_id: int,
        target_position: str,
        interview_mode: str = "comprehensive",
        question_count: int = 20,
    ) -> None:
        """Set the target position for a session and confirms it.

        Raises ValueError with a user-facing message on failure.
        """
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            raise ValueError("会话不存在")
        if session.resume_id is None:
            raise ValueError("请先绑定一份简历")

        await self.session_repo.update_target_position(
            session_id,
            target_position=target_position,
            interview_mode=interview_mode,
            question_count=question_count,
        )
        await self.db.commit()

    # ── Position Suggestion ──

    async def _suggest_position_from_resume(
        self, session_id: int
    ) -> str | None:
        """Extract a suggested target position from the bound resume."""
        session = await self.session_repo.get_by_id(session_id)
        if session is None or session.resume_id is None:
            return None

        report = await self.report_repo.get_by_resume_id(session.resume_id)
        if report is None or not report.summary_json:
            return None

        structured = report.summary_json
        basic = structured.get("basic_info", {})
        target_role = basic.get("target_role", "")
        current_role = basic.get("current_role", "")

        # Use LLM to find the single best target position
        try:
            import json as _json
            basic_info = structured.get("basic_info", {})
            projects = structured.get("projects", [])
            skills = structured.get("skills", {})
            highlights = structured.get("highlights", [])

            # Build a very short summary for the LLM
            summary_parts = []
            if basic_info.get("current_role"):
                summary_parts.append(f"当前岗位: {basic_info['current_role']}")
            if basic_info.get("target_role"):
                summary_parts.append(f"目标岗位(原始): {basic_info['target_role']}")
            if isinstance(skills, dict):
                techs = []
                for key in ("languages", "frameworks", "ai_ml"):
                    vals = skills.get(key, [])
                    if isinstance(vals, list):
                        techs.extend(vals[:3])
                if techs:
                    summary_parts.append(f"技术栈: {', '.join(techs[:6])}")
            if isinstance(projects, list) and projects:
                p_names = [p.get("name", "") for p in projects[:2] if isinstance(p, dict)]
                if p_names:
                    summary_parts.append(f"项目: {', '.join(p_names)}")
            if isinstance(highlights, list) and highlights:
                summary_parts.append(f"亮点: {', '.join(highlights[:2])}")

            summary = "; ".join(summary_parts)
            prompt = (
                "根据简历信息，用15字以内给出候选人最可能的1个面试岗位名称。"
                "只返回岗位名，不要解释。\n\n"
                f"{summary}"
            )
            result = await self.llm.chat([{"role": "user", "content": prompt}])
            pos = result.strip().replace("'", "").replace('"', "").split("\n")[0]
            # Take first line, max 30 chars
            pos = pos.replace("、", ",").replace("；", ",").split(",")[0].strip()
            return pos[:30] if pos else "技术开发"
        except Exception:
            logger.warning("LLM position suggestion failed, using fallback")
            if current_role:
                return current_role
            return "技术开发"

    # ── Helpers ──

    @staticmethod
    def _sse(event: str, data: dict | list) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @staticmethod
    def _session_to_dict(session: InterviewSession) -> dict:
        return {
            "id": session.id,
            "user_id": session.user_id,
            "resume_id": session.resume_id,
            "title": session.title,
            "status": session.status,
            "current_question_index": session.current_question_index,
            "question_generation_status": session.question_generation_status,
            "question_generation_error": session.question_generation_error,
            "target_position": session.target_position,
            "target_position_confirmed": session.target_position_confirmed,
            "interview_mode": session.interview_mode,
            "interview_plan_json": session.interview_plan_json,
            "planner_trace_json": session.planner_trace_json,
            "question_count": session.question_count,
            "memory_summary": session.memory_summary,
            "turn_count": session.turn_count,
            "last_compressed_turn": session.last_compressed_turn,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    @staticmethod
    def _msg_to_dict(msg: InterviewMessage) -> dict:
        return {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "metadata_json": msg.metadata_json,
            "turn_index": msg.turn_index,
            "created_at": msg.created_at,
        }

    @staticmethod
    def _question_to_dict(q: InterviewSessionQuestion) -> dict:
        return {
            "id": q.id,
            "session_id": q.session_id,
            "question_index": q.question_index,
            "question": q.question,
            "standard_answer": q.standard_answer,
            "dimension": q.dimension,
            "difficulty": q.difficulty,
            "source": q.source,
            "evidence_json": q.evidence_json,
            "follow_up_count": q.follow_up_count,
            "parent_question_id": q.parent_question_id,
            "is_dynamic": q.is_dynamic,
            "planned_order": q.planned_order,
            "answer_summary": q.answer_summary,
            "missing_points_json": q.missing_points_json,
            "evaluation_json": q.evaluation_json,
            "status": q.status,
            "created_at": q.created_at,
            "updated_at": q.updated_at,
        }


# ── Module-level SSE helper (shared by both services) ──


def _sse(event: str, data: dict | list) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Background question generation (with fresh DB session) ──


async def _generate_questions_background(
    session_id: int,
    llm,
    embedding,
    vector_store,
    prompt_builder: InterviewPromptBuilder,
    memory_manager: InterviewMemoryManager,
) -> None:
    """Run question generation in background with a fresh DB session."""
    try:
        async with async_session_factory() as db:
            session_repo = InterviewSessionRepository(db)
            msg_repo = InterviewMessageRepository(db)
            question_repo = InterviewSessionQuestionRepository(db)
            report_repo = ResumeReportRepository(db)
            resume_repo = ResumeRepository(db)

            chat_service = InterviewChatService(
                db=db,
                llm=llm,
                embedding=embedding,
                vector_store=vector_store,
                session_repo=session_repo,
                msg_repo=msg_repo,
                question_repo=question_repo,
                report_repo=report_repo,
                resume_repo=resume_repo,
                prompt_builder=prompt_builder,
                memory_manager=memory_manager,
            )
            await chat_service.generate_plan(session_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Background question generation failed for session %s", session_id
        )


# ── InterviewChatService: Question-driven chat ──


class InterviewChatService:
    """Question-driven interview chat: pre-generated question queue, evaluation,
    follow-up / next-question / complete decision logic."""

    def __init__(
        self,
        db: AsyncSession,
        llm,
        embedding,
        vector_store,
        session_repo: InterviewSessionRepository,
        msg_repo: InterviewMessageRepository,
        question_repo: InterviewSessionQuestionRepository,
        report_repo: ResumeReportRepository,
        resume_repo: ResumeRepository,
        prompt_builder: InterviewPromptBuilder,
        memory_manager: InterviewMemoryManager,
    ):
        self.db = db
        self.llm = llm
        self.embedding = embedding
        self.vector_store = vector_store
        self.session_repo = session_repo
        self.msg_repo = msg_repo
        self.question_repo = question_repo
        self.report_repo = report_repo
        self.resume_repo = resume_repo
        self.prompt_builder = prompt_builder
        self.memory_manager = memory_manager

    # ── Lightweight Interview Plan (rule-based, no LLM) ──

    async def _init_lightweight_plan(self, session_id: int) -> dict:
        """Initialize interview_plan_json from resume data without LLM.

        Phase 3.5b: The plan is a lightweight internal structure for tracking
        dimensions and budget. No LLM calls, no user-visible wait.
        """
        session = await self.session_repo.get_by_id(session_id)
        if not session or not session.resume_id:
            return {}

        report = await self.report_repo.get_by_resume_id(session.resume_id)
        if not report or not report.summary_json:
            return {}

        structured = report.summary_json
        skills = structured.get("skills", {})
        projects = structured.get("projects", [])
        budget = session.question_count or settings.interview_question_count

        dimensions = []

        # Extract from skills
        if isinstance(skills, dict):
            all_techs = []
            for key in ("languages", "frameworks", "databases", "tools", "ai_ml"):
                vals = skills.get(key, [])
                if isinstance(vals, list):
                    all_techs.extend(vals)
            # Top skills get dimension slots
            for tech in all_techs[:5]:
                dimensions.append({
                    "name": f"{tech} 技术深度",
                    "planned_count": max(1, budget // max(len(all_techs[:5]), 1)),
                    "asked_count": 0,
                    "priority": "MEDIUM",
                    "reason": f"简历技术栈包含 {tech}",
                    "search_queries": [f"{tech} 面试题", f"{tech} 技术原理"],
                })

        # Extract from projects
        if isinstance(projects, list):
            for p in projects[:3]:
                name = p.get("name", "") if isinstance(p, dict) else ""
                if name:
                    dimensions.append({
                        "name": f"项目深挖: {name}",
                        "planned_count": max(1, budget // 6),
                        "asked_count": 0,
                        "priority": "HIGH",
                        "reason": f"简历核心项目",
                        "search_queries": [f"{name} 技术架构", f"项目经验 面试"],
                    })

        # Add behavioral and risk dimensions
        if structured.get("risk_points"):
            dimensions.append({
                "name": "简历风险点考察",
                "planned_count": 2,
                "asked_count": 0,
                "priority": "MEDIUM",
                "reason": "简历存在风险点",
                "search_queries": ["行为面试 项目挑战", "技术短板 面试"],
            })

        plan = {
            "target_position": session.target_position or "",
            "interview_mode": session.interview_mode,
            "question_budget": budget,
            "strategy": {"question_generation": "on_demand"},
            "dimensions": dimensions,
            "_kb_cache": {},
        }
        await self.session_repo.save_plan(session_id, plan_json=plan, trace_json=None)
        return plan

    # ── Interview Plan Generation (legacy compat) ──

    async def generate_plan(self, session_id: int) -> None:
        """Generate lightweight interview plan (rule-based, no LLM).

        Phase 3.5b: Plan is rule-based from resume data. Used by background tasks.
        """
        try:
            session = await self.session_repo.get_by_id(session_id)
            if session is None or session.resume_id is None:
                return
            resume = await self.resume_repo.get_by_id(session.resume_id)
            if resume is None or resume.status != "COMPLETED":
                await self.session_repo.update_question_generation_status(session_id, "FAILED", "简历未处理完成")
                await self.db.commit()
                return
            await self._init_lightweight_plan(session_id)
            await self.session_repo.update_question_generation_status(session_id, "READY")
            await self.db.commit()
        except Exception as e:
            logger.exception("Plan generation failed for session %s", session_id)
            try:
                await self.session_repo.update_question_generation_status(session_id, "FAILED", str(e))
                await self.db.commit()
            except Exception:
                logger.exception("Failed to update FAILED status")

    # ── On-Demand Question Generation ──

    # ── Confirm Position + Generate Q1 ──

    async def confirm_and_generate_first_question(
        self,
        session_id: int,
        target_position: str,
        interview_mode: str = "comprehensive",
        question_count: int = 20,
    ) -> dict | None:
        """Confirm target position and immediately generate Q1.

        Phase 3.5b: No separate plan generation wait. No "start interview" step.
        Returns Q1 directly so frontend can display it immediately.
        """
        session = await self.session_repo.get_by_id(session_id)
        if session is None or session.resume_id is None:
            return {"error": "SESSION_INVALID", "message": "会话不存在或未绑定简历"}

        resume = await self.resume_repo.get_by_id(session.resume_id)
        if resume is None or resume.status != "COMPLETED":
            return {"error": "RESUME_NOT_READY", "message": "简历未处理完成"}

        try:
            # 1. Save position + confirm
            await self.session_repo.update_target_position(
                session_id,
                target_position=target_position,
                interview_mode=interview_mode,
                question_count=question_count,
            )
            from sqlalchemy import update
            await self.db.execute(
                update(InterviewSession)
                .where(InterviewSession.id == session_id)
                .values(target_position_confirmed=True)
            )
            await self.db.flush()

            # 2. Init lightweight plan (rule-based, no LLM)
            await self._init_lightweight_plan(session_id)

            # 3. Generate Q1
            await self.session_repo.update_question_generation_status(session_id, "GENERATING_QUESTION")
            await self.db.commit()

            q_data = await self.generate_next_question(session_id)
            if not q_data:
                await self.session_repo.update_question_generation_status(session_id, "FAILED", "第一题生成失败")
                await self.db.commit()
                return {"error": "QUESTION_GENERATION_FAILED", "message": "第一题生成失败，请重试"}

            # 4. Mark Q1 ASKED, set index
            await self.question_repo.update_status(q_data["question_id"], "ASKED")
            await self.session_repo.update_current_question_index(session_id, 0)
            await self.session_repo.update_question_generation_status(session_id, "READY")

            # 5. Welcome message
            await self.msg_repo.create(
                session_id=session_id, role="ASSISTANT",
                content=f"面试开始。第一题：{q_data['question']}",
                metadata_json={"source": "QUESTION_DRIVEN", "question_id": q_data["question_id"]},
                turn_index=0,
            )
            await self.db.commit()

            return {
                "target_position_confirmed": True,
                "question_budget": question_count,
                "current_question": {
                    "question_id": q_data["question_id"],
                    "question_index": 0,
                    "question": q_data["question"],
                    "dimension": q_data["dimension"],
                    "difficulty": q_data["difficulty"],
                    "source": q_data["source"],
                    "evidence": q_data.get("evidence"),
                },
            }
        except Exception:
            logger.exception("confirm_and_generate_first_question failed for session %s", session_id)
            try:
                await self.session_repo.update_question_generation_status(session_id, "FAILED", str(Exception))
                await self.db.commit()
            except Exception:
                pass
            return {"error": "GENERATION_FAILED", "message": "生成失败，请重试"}

    async def generate_next_question(
        self,
        session_id: int,
        dimension_hint: str | None = None,
        is_dynamic: bool = False,
        parent_question_id: int | None = None,
        user_answer: str = "",
        on_token: callable = None,
    ) -> dict | None:
        """Generate ONE question on demand (Phase 3.5).

        Called when:
        - Starting interview (first question)
        - Controller decides NEXT_QUESTION
        - Controller decides INSERT_DYNAMIC_QUESTION

        Reads interview_plan_json, picks next dimension, retrieves KB,
        calls LLM to generate 1 question + standard_answer, saves to DB.
        """
        session = await self.session_repo.get_by_id(session_id)
        if session is None or not session.interview_plan_json:
            return None

        plan = session.interview_plan_json
        dimensions = plan.get("dimensions", [])
        existing = await self.question_repo.get_by_session_id(session_id)

        # Pick next dimension
        if dimension_hint:
            dim = next((d for d in dimensions if d.get("name") == dimension_hint), None)
        else:
            dim = None
        if not dim:
            # Pick first dimension below planned_count, sorted by priority
            high_first = sorted(dimensions, key=lambda d: (
                0 if d.get("priority") == "HIGH" else 1
            ))
            for d in high_first:
                if d.get("asked_count", 0) < d.get("planned_count", 0):
                    dim = d
                    break
        if not dim:
            dim = dimensions[0] if dimensions else {"name": "综合", "search_queries": [], "planned_count": 1, "asked_count": 0}

        dim_name = dim.get("name", "综合")
        search_queries = dim.get("search_queries", [])

        # Retrieve KB for this dimension
        kb_context = ""
        kb_cache = plan.get("_kb_cache", {})
        if dim_name in kb_cache and kb_cache[dim_name]:
            hits = kb_cache[dim_name][:5]
            kb_context = "\n".join(
                f"[{h.get('title', '')}] {h.get('preview', '')}"
                for h in hits
            )

        # Determine question index
        question_index = len(existing)
        next_index = max((q.question_index for q in existing), default=-1) + 1

        # Get resume data
        report = await self.report_repo.get_by_resume_id(session.resume_id)
        structured = report.summary_json if report else None
        resume_text = ""
        if structured:
            resume_text = json.dumps(structured, ensure_ascii=False)[:2000]

        # Get completed questions summary
        completed = [q for q in existing if q.status in ("ANSWERED",)]
        completed_text = ""
        for q in completed[:10]:
            completed_text += f"Q{q.question_index}: {q.question[:100]} | 评价: {q.answer_summary or '无'}\n"

        # Build concise prompt (Phase 3.5b: keep it short for fast generation)
        prompt = (
            f"你是技术面试官。为目标岗位「{session.target_position or '未指定'}」的候选人"
            f"出一道「{dim_name}」维度的面试题。\n\n"
            f"简历: {resume_text[:1000]}\n\n"
            f"KB参考: {kb_context[:500] or '（无）'}\n"
            f"历史: {completed_text[:300] or '（无）'}\n"
            f"最近回答: {user_answer[:200] or '（开始面试）'}\n\n"
            f"返回JSON（只一道题，必须含standard_answer 3-5个要点）:\n"
            f'{{"question":"...","standard_answer":"...","dimension":"{dim_name}",'
            f'"difficulty":"MEDIUM","source":"LLM_GENERATED"}}'
        )

        try:
            # Stream LLM response for progress (use chat_stream to avoid 30s idle)
            response = ""
            async for token in self.llm.chat_stream(
                [{"role": "user", "content": prompt}]
            ):
                response += token
                if on_token:
                    await on_token(token)
            data = self._parse_decision(response)
            if not data.get("question"):
                return None

            q = {
                "question_index": next_index,
                "question": data["question"],
                "standard_answer": data.get("standard_answer", ""),
                "dimension": data.get("dimension", dim_name),
                "difficulty": data.get("difficulty", "MEDIUM"),
                "source": data.get("source", "LLM_GENERATED"),
                "evidence_json": data.get("evidence"),
                "status": "PENDING",
                "is_dynamic": is_dynamic,
                "parent_question_id": parent_question_id,
                "planned_order": next_index,
            }
            # Actually create in DB
            result = await self.question_repo.batch_create(session_id, [q])
            question_obj = result[0] if result else None
            if not question_obj:
                return None

            # Update dimension asked_count
            dim["asked_count"] = dim.get("asked_count", 0) + 1
            await self.session_repo.save_plan(session_id, plan_json=plan, trace_json=None)

            return {
                "question_id": question_obj.id,
                "question_index": question_obj.question_index,
                "question": question_obj.question,
                "standard_answer": question_obj.standard_answer,
                "dimension": question_obj.dimension,
                "difficulty": question_obj.difficulty,
                "source": question_obj.source,
                "evidence": question_obj.evidence_json,
                "is_dynamic": is_dynamic,
                "total_questions": plan.get("question_budget", 20),
            }
        except Exception:
            logger.exception("Failed to generate next question for session %s", session_id)
            return None

    # ── Legacy: trigger from router ──
    async def generate_questions(self, session_id: int) -> None:
        """Legacy compat: calls generate_plan (Phase 3.5 on-demand mode)."""
        await self.generate_plan(session_id)

    # ── Start Interview ──

    async def start_interview(
        self, session_id: int, user_id: int
    ) -> dict | None:
        """Start the interview: generate and return first question (on-demand)."""
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None

        resume = await self.resume_repo.get_by_id(session.resume_id)
        if resume is None or resume.status != "COMPLETED":
            return None

        if not session.target_position_confirmed:
            return {"error": "TARGET_POSITION_REQUIRED", "message": "请先确认本次面试岗位"}

        gen_status = session.question_generation_status
        if gen_status == "PENDING":
            return {"error": "TARGET_POSITION_REQUIRED", "message": "请先确认面试岗位"}
        elif gen_status == "FAILED":
            return {"error": "QUESTIONS_NOT_READY", "sub_code": "FAILED", "message": "题目生成失败，请重试"}

        # Generate Q1 on demand
        await self.session_repo.update_question_generation_status(session_id, "GENERATING_QUESTION")
        await self.db.commit()

        q_data = await self.generate_next_question(session_id)
        if not q_data:
            await self.session_repo.update_question_generation_status(session_id, "FAILED", "第一题生成失败")
            await self.db.commit()
            return {"error": "QUESTION_GENERATION_FAILED", "message": "第一题生成失败"}

        # Mark ASKED
        q_id = q_data["question_id"]
        await self.question_repo.update_status(q_id, "ASKED")
        await self.session_repo.update_current_question_index(session_id, 0)
        await self.session_repo.update_question_generation_status(session_id, "READY")

        # Welcome message
        await self.msg_repo.create(
            session_id=session_id, role="ASSISTANT",
            content=f"开始面试。第一题：{q_data['question']}",
            metadata_json={"source": "QUESTION_DRIVEN", "question_id": q_id},
            turn_index=0,
        )
        await self.db.commit()

        return {
            "type": "QUESTION",
            "question_id": q_data["question_id"],
            "question_index": q_data["question_index"],
            "total_questions": q_data.get("total_questions", 20),
            "question": q_data["question"],
            "dimension": q_data["dimension"],
            "difficulty": q_data["difficulty"],
            "source": q_data["source"],
            "evidence": q_data.get("evidence"),
        }

    # ── Chat Stream (Question-Driven) ──

    async def chat_stream(
        self,
        session_id: int,
        user_id: int,
        content: str,
    ):
        """Yields SSE events for question-driven interview chat flow."""
        # Validate session
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            yield _sse("error", {"code": "SESSION_NOT_FOUND", "message": "会话不存在"})
            return

        if session.resume_id is None:
            yield _sse("error", {"code": "NO_RESUME", "message": "请先上传并绑定一份简历"})
            return

        resume = await self.resume_repo.get_by_id(session.resume_id)
        if resume is None or resume.status != "COMPLETED":
            yield _sse("error", {"code": "RESUME_NOT_READY", "message": "简历已被删除或未处理完成"})
            return

        if not session.target_position_confirmed:
            # Save user message first
            new_turn = session.turn_count + 1
            await self.msg_repo.create(
                session_id=session_id,
                role="USER",
                content=content,
                turn_index=new_turn,
            )
            await self.db.flush()
            # Let LLM handle the position confirmation dialog
            async for evt in self._handle_position_confirmation(
                session, content, new_turn
            ):
                yield evt
            return

        # Check readiness (Phase 3.5b: READY or GENERATING_QUESTION means OK)
        gen_status = session.question_generation_status
        if gen_status not in ("READY", "GENERATING_QUESTION"):
            yield _sse("error", {
                "code": "QUESTIONS_NOT_READY",
                "sub_code": gen_status,
                "message": {
                    "PENDING": "请先确认面试岗位",
                    "FAILED": f"生成失败: {session.question_generation_error or '未知错误'}",
                }.get(gen_status, "题目状态异常"),
            })
            return

        # Get current question
        q = await self.question_repo.get_by_index(
            session_id, session.current_question_index
        )
        if q is None:
            yield _sse("interview_complete", {
                "total_questions": 0,
                "answered_count": 0,
                "avg_score": 0,
            })
            return

        all_questions = await self.question_repo.get_by_session_id(session_id)

        # Save user message
        new_turn = session.turn_count + 1
        await self.msg_repo.create(
            session_id=session_id,
            role="USER",
            content=content,
            turn_index=new_turn,
        )
        await self.db.flush()

        try:
            # Get resume data
            report = await self.report_repo.get_by_resume_id(session.resume_id)
            structured = report.summary_json if report else None
            raw_text = resume.raw_text or ""

            # Phase 3.6: Rule-based SKIP_QUESTION detection
            skip_patterns = [
                "不熟", "不会", "这个不会", "换一个", "换个问题", "下一个问题",
                "跳过", "这个问题我不太了解", "能不能问别的", "换一题", "不知道",
                "不太清楚", "不了解", "没做过", "没接触过", "换一题吧",
            ]
            user_wants_skip = any(p in content for p in skip_patterns)
            if user_wants_skip:
                await self.question_repo.update_status(q.id, "SKIPPED")
                await self.question_repo.update_evaluation(
                    q.id, status="SKIPPED",
                    evaluation_json={"action": "SKIP_QUESTION", "reason": "user_requested_skip"},
                )
                # Generate next question
                yield _sse("status", {"stage": "generating_next_question"})
                next_q_data = await self.generate_next_question(
                    session_id, user_answer=content,
                )
                if next_q_data:
                    new_index = session.current_question_index + 1
                    await self.session_repo.update_current_question_index(session_id, new_index)
                    await self.question_repo.update_status(next_q_data["question_id"], "ASKED")
                    await self.session_repo.update_question_generation_status(session_id, "READY")
                    total_budget = session.question_count or 20
                    yield _sse("question_transition", {"from_index": session.current_question_index, "to_index": new_index, "preview": ""})
                    yield _sse("question", {
                        "question_id": next_q_data["question_id"], "question_index": new_index,
                        "total_questions": total_budget, "question": next_q_data["question"],
                        "source": next_q_data["source"], "dimension": next_q_data["dimension"],
                        "difficulty": next_q_data["difficulty"], "evidence": next_q_data.get("evidence"),
                    })
                    await self.msg_repo.create(
                        session_id=session_id, role="ASSISTANT",
                        content=next_q_data["question"],
                        metadata_json={"source": "QUESTION_DRIVEN", "type": "QUESTION",
                                       "question_id": next_q_data["question_id"],
                                       "dimension": next_q_data["dimension"],
                                       "difficulty": next_q_data["difficulty"]},
                        turn_index=new_turn,
                    )
                else:
                    yield _sse("error", {"code": "QUESTION_GENERATION_FAILED", "message": "下一题生成失败，请重试"})
                await self.session_repo.increment_turn(session_id)
                await self.db.commit()
                yield _sse("done", {"message_id": 0, "turn_index": new_turn, "action": "SKIP_QUESTION"})
                return

            recent_msgs = await self.msg_repo.get_recent_messages(session_id, limit=20)

            # Determine mode: follow-up or first-answer evaluation
            is_follow_up = self._is_follow_up_mode(recent_msgs)

            # Build question summaries (no standard_answer)
            all_summaries = await self.question_repo.get_question_summaries(session_id)
            completed = [s for s in all_summaries if s["status"] in ("ANSWERED", "ASKED")]
            remaining = [s for s in all_summaries if s["status"] in ("PENDING",)]
            completed_text = self.prompt_builder.build_question_summary_text(
                completed, "completed"
            )
            remaining_text = self.prompt_builder.build_question_summary_text(
                remaining, "remaining"
            )

            if is_follow_up:
                # ── Follow-up mode: retrieve KB for deeper questioning ──
                yield _sse("status", {"stage": "retrieving_kb"})
                retrieved_chunks = await self._retrieve_kb(
                    structured, content, recent_msgs
                )
                yield _sse("retrieval", {
                    "hit_count": len(retrieved_chunks),
                    "source": self.prompt_builder.extract_source_label(retrieved_chunks),
                })

                yield _sse("status", {"stage": "generating"})
                prompt = self.prompt_builder.build_follow_up_prompt(
                    current_question=q.question,
                    standard_answer=q.standard_answer or "",
                    resume_structured=structured,
                    resume_raw_text=raw_text,
                    memory_summary=session.memory_summary,
                    recent_messages=recent_msgs,
                    user_answer=content,
                    retrieved_context=retrieved_chunks,
                    target_position=session.target_position or "",
                    interview_mode=session.interview_mode,
                    completed_summaries=completed_text,
                    remaining_summaries=remaining_text,
                )
            else:
                # ── Evaluation mode: use standard_answer as rubric ──
                yield _sse("status", {"stage": "evaluating"})
                prompt = self.prompt_builder.build_evaluation_prompt(
                    current_question=q.question,
                    standard_answer=q.standard_answer or "",
                    resume_structured=structured,
                    resume_raw_text=raw_text,
                    memory_summary=session.memory_summary,
                    recent_messages=recent_msgs,
                    user_answer=content,
                    target_position=session.target_position or "",
                    interview_mode=session.interview_mode,
                    completed_summaries=completed_text,
                    remaining_summaries=remaining_text,
                )

            # Accumulate LLM response (don't stream raw JSON to frontend)
            llm_messages = [{"role": "user", "content": prompt}]
            full_content = ""

            async for token in self.llm.chat_stream(llm_messages):
                full_content += token

            # Parse LLM decision
            decision = self._parse_decision(full_content)

            action = decision.get("action", "NEXT_QUESTION")
            evaluation_text = decision.get("evaluation", "")
            score = decision.get("score", 0)

            # Format evaluation as readable Markdown, stream as tokens
            md_lines = []
            if evaluation_text:
                md_lines.append(f"**面试官点评**\n{evaluation_text}")
            if score:
                md_lines.append(f"\n**得分**: {score} / 5")
            missing = decision.get("missing_points") or []
            if missing:
                md_lines.append("\n**缺失点**:")
                for mp in missing:
                    md_lines.append(f"- {mp}" if isinstance(mp, str) else f"- {mp}")
            risk = decision.get("risk_tip")
            if risk:
                md_lines.append(f"\n**风险提示**: {risk}")

            eval_md = "\n".join(md_lines)
            # Stream evaluation as tokens (small chunks for smooth display)
            for i in range(0, len(eval_md), 10):
                yield _sse("token", {"content": eval_md[i:i+10]})

            yield _sse("evaluation", {
                "score": score,
                "evaluation": evaluation_text,
                "covered_points": decision.get("covered_points", []),
                "missing_points": decision.get("missing_points", []),
                "risk_tip": decision.get("risk_tip"),
                "action": action,
            })

            # Handle actions
            if action == "FOLLOW_UP":
                if q.follow_up_count >= settings.interview_max_follow_ups_per_question:
                    action = "NEXT_QUESTION"
                else:
                    await self.question_repo.increment_follow_up(q.id)
                    new_count = q.follow_up_count + 1
                    follow_up_q = decision.get("follow_up_question", "请进一步说明")
                    yield _sse("follow_up", {
                        "question": follow_up_q,
                        "follow_up_count": new_count,
                        "max_follow_ups": settings.interview_max_follow_ups_per_question,
                    })

            if action == "INSERT_DYNAMIC_QUESTION":
                yield _sse("status", {"stage": "generating_dynamic_question"})
                dynamic_q_data = await self.generate_next_question(
                    session_id, is_dynamic=True, parent_question_id=q.id,
                    user_answer=content,
                )
                if dynamic_q_data:
                    yield _sse("dynamic_question", {
                        "question_id": dynamic_q_data["question_id"],
                        "question_index": dynamic_q_data["question_index"],
                        "question": dynamic_q_data["question"],
                        "source": dynamic_q_data["source"],
                        "dimension": dynamic_q_data["dimension"],
                        "difficulty": dynamic_q_data["difficulty"],
                        "parent_question_id": q.id,
                        "reason": "基于你的回答临时追问",
                    })

            if action == "NEXT_QUESTION":
                # Save evaluation for current question
                await self.question_repo.update_evaluation(
                    q.id,
                    answer_summary=json.dumps(
                        decision.get("covered_points", []), ensure_ascii=False
                    ),
                    missing_points_json=decision.get("missing_points"),
                    evaluation_json={
                        "score": score,
                        "evaluation": evaluation_text,
                        "risk_tip": decision.get("risk_tip"),
                    },
                    status="ANSWERED",
                )

                # Phase 3.5: generate next question on-demand
                yield _sse("status", {"stage": "generating_next_question"})
                dim_hint = decision.get("next_dimension_hint")
                total_budget = session.interview_plan_json.get("question_budget", 20) if session.interview_plan_json else 20
                next_q_data = await self.generate_next_question(
                    session_id, dimension_hint=dim_hint, user_answer=content,
                )
                if next_q_data:
                    new_index = session.current_question_index + 1
                    await self.session_repo.update_current_question_index(session_id, new_index)
                    await self.question_repo.update_status(next_q_data["question_id"], "ASKED")
                    await self.session_repo.update_question_generation_status(session_id, "READY")

                    yield _sse("question_transition", {
                        "from_index": session.current_question_index,
                        "to_index": new_index,
                        "preview": "",
                    })
                    yield _sse("question", {
                        "question_id": next_q_data["question_id"],
                        "question_index": new_index,
                        "total_questions": total_budget,
                        "question": next_q_data["question"],
                        "source": next_q_data["source"],
                        "dimension": next_q_data["dimension"],
                        "difficulty": next_q_data["difficulty"],
                        "evidence": next_q_data.get("evidence"),
                    })
                    # Save question as ASSISTANT message
                    await self.msg_repo.create(
                        session_id=session_id, role="ASSISTANT",
                        content=next_q_data["question"],
                        metadata_json={
                            "source": "QUESTION_DRIVEN", "type": "QUESTION",
                            "question_id": next_q_data["question_id"],
                            "dimension": next_q_data["dimension"],
                            "difficulty": next_q_data["difficulty"],
                            "source_label": next_q_data["source"],
                        },
                        turn_index=new_turn,
                    )
                else:
                    yield _sse("error", {"code": "QUESTION_GENERATION_FAILED", "message": "下一题生成失败，请重试"})
                    action = "NONE"

            if action == "COMPLETE":
                await self.question_repo.update_evaluation(
                    q.id,
                    answer_summary=json.dumps(
                        decision.get("covered_points", []), ensure_ascii=False
                    ),
                    missing_points_json=decision.get("missing_points"),
                    evaluation_json={
                        "score": score,
                        "evaluation": evaluation_text,
                        "risk_tip": decision.get("risk_tip"),
                    },
                    status="ANSWERED",
                )
                answered = [
                    x for x in all_questions
                    if x.status in ("ANSWERED", "ASKED")
                ]
                avg_score = score
                yield _sse("interview_complete", {
                    "total_questions": len(all_questions),
                    "answered_count": len(answered),
                    "avg_score": avg_score,
                })

            # Save assistant message (Phase 3.6: Markdown format, structured metadata)
            if action == "FOLLOW_UP":
                assistant_content = (
                    f"{eval_md}\n\n**追问**\n{decision.get('follow_up_question', '')}"
                )
                msg_type = "FOLLOW_UP"
            elif action == "INSERT_DYNAMIC_QUESTION":
                assistant_content = eval_md
                msg_type = "EVALUATION"
            else:
                assistant_content = eval_md
                msg_type = "EVALUATION"

            await self.msg_repo.create(
                session_id=session_id,
                role="ASSISTANT",
                content=assistant_content,
                metadata_json={
                    "source": "QUESTION_DRIVEN",
                    "type": msg_type,
                    "question_id": q.id,
                    "action": action,
                    "score": score,
                    "is_follow_up": is_follow_up,
                    "covered_points": decision.get("covered_points", []),
                    "missing_points": decision.get("missing_points", []),
                    "risk_tip": decision.get("risk_tip"),
                },
                turn_index=new_turn,
            )

            await self.session_repo.increment_turn(session_id)

            # Memory compression (same logic as before)
            compressed = False
            if self.memory_manager.should_compress(
                new_turn, session.last_compressed_turn
            ):
                try:
                    all_msgs = await self.msg_repo.get_by_session_id(session_id)
                    to_compress, _ = self.memory_manager.partition_messages(
                        all_msgs, session.last_compressed_turn, new_turn
                    )
                    if to_compress:
                        new_summary = await self.memory_manager.compress(
                            self.llm, to_compress, session.memory_summary
                        )
                        new_last = new_turn - self.memory_manager.recent_keep_count
                        await self.session_repo.update_memory_summary(
                            session_id, new_summary, max(new_last, 0)
                        )
                        compressed = True
                        yield _sse("compressed", {
                            "compressed_turns": len(to_compress),
                            "new_last_compressed_turn": max(new_last, 0),
                        })
                except Exception:
                    logger.exception("Memory compression failed, continuing")

            await self.db.commit()

            yield _sse("done", {
                "message_id": 0,
                "turn_index": new_turn,
                "action": action,
                "compressed": compressed,
            })

        except Exception as e:
            await self.db.rollback()
            logger.exception("Interview chat stream error")
            yield _sse("error", {"code": "INTERVIEW_ERROR", "message": str(e)})

    # ── Question access methods ──

    async def get_current_question(
        self, session_id: int, user_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        q = await self.question_repo.get_by_index(
            session_id, session.current_question_index
        )
        if q is None:
            return {"error": "NO_QUESTIONS", "message": "暂无题目"}
        all_questions = await self.question_repo.get_by_session_id(session_id)
        return {
            "question_id": q.id,
            "question_index": q.question_index,
            "total_questions": len(all_questions),
            "question": q.question,
            "source": q.source,
            "dimension": q.dimension,
            "difficulty": q.difficulty,
            "evidence": q.evidence_json,
            "status": q.status,
            "follow_up_count": q.follow_up_count,
        }

    async def get_question_list(
        self, session_id: int, user_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        questions = await self.question_repo.get_by_session_id(session_id)
        result = []
        for q in questions:
            item = InterviewService._question_to_dict(q)
            # Mask standard_answer for questions not yet ASKED/ANSWERED
            if q.status not in ("ASKED", "ANSWERED"):
                item["standard_answer"] = None
            result.append(item)
        return {
            "questions": result,
            "total": len(result),
            "current_question_index": session.current_question_index,
            "question_generation_status": session.question_generation_status,
        }

    async def reveal_answer(
        self, session_id: int, user_id: int, question_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        q = await self.question_repo.get_by_id(question_id)
        if q is None or q.session_id != session_id:
            return None
        if q.status not in ("ASKED", "ANSWERED"):
            return {"error": "NOT_REVEALED", "message": "该题目尚未提问，暂不可查看答案"}
        return {
            "question_id": q.id,
            "question": q.question,
            "standard_answer": q.standard_answer,
            "source": q.source,
            "evidence_json": q.evidence_json,
        }

    async def skip_question(
        self, session_id: int, user_id: int, question_id: int
    ) -> dict | None:
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None
        q = await self.question_repo.get_by_id(question_id)
        if q is None or q.session_id != session_id:
            return None
        await self.question_repo.update_status(question_id, "SKIPPED")
        new_index = session.current_question_index + 1
        await self.session_repo.update_current_question_index(session_id, new_index)
        await self.db.commit()

        next_q = await self.question_repo.get_by_index(session_id, new_index)
        if next_q:
            await self.question_repo.update_status(next_q.id, "ASKED")
            all_questions = await self.question_repo.get_by_session_id(session_id)
            return {
                "skipped_question_id": question_id,
                "next_question": {
                    "question_id": next_q.id,
                    "question_index": new_index,
                    "total_questions": len(all_questions),
                    "question": next_q.question,
                    "source": next_q.source,
                    "dimension": next_q.dimension,
                    "difficulty": next_q.difficulty,
                    "evidence": next_q.evidence_json,
                },
            }
        return {
            "skipped_question_id": question_id,
            "next_question": None,
            "message": "已是最后一题",
        }

    # ── Internal helpers ──

    async def _handle_position_confirmation(
        self, session: InterviewSession, user_message: str, turn_index: int
    ):
        """Handle position confirmation dialog via LLM.

        When target_position is not yet confirmed, this method:
        1. Asks LLM to determine if user confirmed or provided a new position
        2. If confirmed → saves confirmed=true, triggers background generation
        3. If new position → updates and confirms, triggers generation
        4. If unclear → LLM responds asking for clarification
        """
        try:
            suggested = session.target_position or "未推测"
            prompt = (
                f"你正在确认候选人的面试目标岗位。\n\n"
                f"系统推测的岗位：{suggested}\n\n"
                f"候选人的回复：{user_message}\n\n"
                f"请判断候选人的意图：\n"
                f"1. 如果候选人确认岗位（如「是的」「可以」「对」「OK」「好」等），返回：CONFIRM\n"
                f"2. 如果候选人给出了新的岗位名称（如「后端开发实习」「Python后端」），返回：NEW_POSITION: <新岗位>\n"
                f"3. 如果候选人问了其他问题，返回：OTHER\n\n"
                f"只返回上述格式，不要其他内容。"
            )
            result = await self.llm.chat([{"role": "user", "content": prompt}])
            result = result.strip()

            if result.startswith("CONFIRM"):
                pos = suggested
            elif result.startswith("NEW_POSITION:"):
                pos = result.replace("NEW_POSITION:", "").strip()
            else:
                pos = None

            if pos:
                yield _sse("token", {"content": f"好的，面试岗位：「{pos}」。\n\n"})

                # Save position + init lightweight plan
                from sqlalchemy import update as sqla_update
                await self.session_repo.update_target_position(
                    session.id, target_position=pos,
                    interview_mode=session.interview_mode,
                    question_count=session.question_count,
                )
                await self.db.execute(
                    sqla_update(InterviewSession)
                    .where(InterviewSession.id == session.id)
                    .values(target_position_confirmed=True)
                )
                await self._init_lightweight_plan(session.id)
                await self.session_repo.update_question_generation_status(
                    session.id, "GENERATING_QUESTION"
                )
                await self.db.commit()

                # Generate Q1, then stream the question text (not raw JSON)
                yield _sse("status", {"stage": "generating_first_question"})
                yield _sse("token", {"content": "。"})  # keep-alive
                q1_data = await self.generate_next_question(session.id)
                if q1_data:
                    # Stream question text character by character
                    q_text = q1_data["question"]
                    for i in range(0, len(q_text), 3):
                        yield _sse("token", {"content": q_text[i:i+3]})

                if q1_data:
                    # Mark Q1 ASKED
                    await self.question_repo.update_status(q1_data["question_id"], "ASKED")
                    await self.session_repo.update_current_question_index(session.id, 0)
                    await self.session_repo.update_question_generation_status(session.id, "READY")
                    await self.db.commit()

                    yield _sse("question", {
                        "question_id": q1_data["question_id"],
                        "question_index": 0,
                        "total_questions": session.question_count,
                        "question": q1_data["question"],
                        "source": q1_data["source"],
                        "dimension": q1_data["dimension"],
                        "difficulty": q1_data["difficulty"],
                        "evidence": q1_data.get("evidence"),
                    })
                    # Save Q1 as ASSISTANT message
                    await self.msg_repo.create(
                        session_id=session.id, role="ASSISTANT",
                        content=q1_data["question"],
                        metadata_json={
                            "source": "QUESTION_DRIVEN", "type": "QUESTION",
                            "question_id": q1_data["question_id"],
                            "dimension": q1_data["dimension"],
                            "difficulty": q1_data["difficulty"],
                        },
                        turn_index=turn_index,
                    )
                    yield _sse("evaluation", {
                        "score": 0,
                        "comment": f"岗位确认：{pos}，面试开始",
                        "action": "INTERVIEW_STARTED",
                    })
                else:
                    yield _sse("evaluation", {
                        "score": 0,
                        "comment": "题目生成失败，请重试",
                        "action": "GENERATION_FAILED",
                    })
                yield _sse("done", {
                    "message_id": 0,
                    "turn_index": turn_index,
                    "action": "INTERVIEW_STARTED",
                })
                return

            else:
                # OTHER — LLM responds naturally asking for position
                clarify_prompt = (
                    f"你是面试助手。系统推测候选人面试岗位是「{suggested}」，但候选人回复不够明确。"
                    f"请友好地请候选人确认岗位：是「{suggested}」吗？或者告诉实际岗位名称。\n\n"
                    f"候选人说：{user_message}\n\n"
                    f"请简短回复（30字内），引导候选人确认岗位。"
                )
                response_text = await self.llm.chat(
                    [{"role": "user", "content": clarify_prompt}]
                )

                yield _sse("token", {"content": response_text})
                yield _sse("evaluation", {
                    "score": 0,
                    "comment": "请先确认岗位",
                    "action": "AWAIT_POSITION",
                })

                await self.msg_repo.create(
                    session_id=session.id,
                    role="ASSISTANT",
                    content=response_text.strip(),
                    metadata_json={
                        "source": "SYSTEM",
                        "type": "POSITION_CLARIFY",
                    },
                    turn_index=turn_index,
                )
                await self.session_repo.increment_turn(session.id)
                await self.db.commit()
                yield _sse("done", {
                    "message_id": 0,
                    "turn_index": turn_index,
                    "action": "AWAIT_POSITION",
                })
                return

        except Exception:
            logger.exception("Position confirmation failed")
            yield _sse("error", {
                "code": "TARGET_POSITION_REQUIRED",
                "message": "请先确认本次面试岗位",
            })

    async def _extract_dimensions(
        self, structured: dict, target_position: str = "", question_count: int = 20
    ) -> list[dict]:
        """Extract interview dimensions from structured resume via LLM."""
        if not settings.interview_dimension_extraction_enabled:
            return self._rule_based_dimensions(structured, target_position)

        try:
            prompt = self.prompt_builder.build_dimension_extraction_prompt(
                structured,
                target_position=target_position,
                question_count=question_count,
            )
            response = await self.llm.chat([{"role": "user", "content": prompt}])
            return self._parse_json_response(response)
        except Exception:
            logger.exception("Dimension extraction via LLM failed, falling back to rules")
            return self._rule_based_dimensions(structured, target_position)

    def _rule_based_dimensions(
        self, structured: dict, target_position: str = ""
    ) -> list[dict]:
        """Fallback: extract dimensions from skills and projects."""
        dimensions: list[dict] = []
        skills = structured.get("skills", {})
        if isinstance(skills, dict):
            tech_keywords = []
            for key in ("languages", "frameworks", "databases", "tools", "ai_ml"):
                vals = skills.get(key, [])
                if isinstance(vals, list):
                    tech_keywords.extend(vals)

            for tech in tech_keywords[:6]:
                dimensions.append({
                    "dimension": f"{tech} 技术深度",
                    "relevance": "HIGH",
                    "search_queries": [f"{tech} 面试题", f"{tech} 技术原理 面试"],
                    "reason": f"简历技术栈中包含 {tech}",
                })

        projects = structured.get("projects", [])
        if isinstance(projects, list) and projects:
            for p in projects[:3]:
                name = p.get("name", "") if isinstance(p, dict) else ""
                if name:
                    dimensions.append({
                        "dimension": f"项目深挖: {name}",
                        "relevance": "MEDIUM",
                        "search_queries": [f"{name} 技术架构 面试", f"项目经验 面试题"],
                        "reason": f"简历项目: {name}",
                    })

        risk_points = structured.get("risk_points", [])
        if isinstance(risk_points, list) and risk_points:
            dimensions.append({
                "dimension": "简历风险点考察",
                "relevance": "MEDIUM",
                "search_queries": ["行为面试题 项目挑战", "技术短板 面试回答"],
                "reason": "简历存在风险点需要验证",
            })

        return dimensions[:6]

    def _extract_questions_from_hits(
        self, grouped_hits: dict[str, list[dict]]
    ) -> tuple[list[dict], set[int]]:
        """Extract question candidates from KB retrieval hits."""
        questions: list[dict] = []
        seen_chunks: set[int] = set()

        for dim_name, hits in grouped_hits.items():
            for hit in hits:
                chunk_id = hit.get("chunk_id")
                if chunk_id is not None:
                    if chunk_id in seen_chunks:
                        continue
                    seen_chunks.add(chunk_id)

                content = hit.get("preview", "") or ""
                title = hit.get("title", "")

                # Check if content looks like a question
                if self._looks_like_question(content) or self._looks_like_question(title):
                    questions.append({
                        "question": title if self._looks_like_question(title) else content[:300],
                        "standard_answer": content[:500] if self._looks_like_question(title) else None,
                        "dimension": dim_name,
                        "difficulty": "MEDIUM",
                        "source": "VECTOR_RETRIEVED",
                        "evidence_json": {
                            "chunk_id": hit.get("chunk_id"),
                            "doc_id": hit.get("doc_id"),
                            "title": hit.get("title", ""),
                            "preview": hit.get("preview", ""),
                            "score": hit.get("score"),
                            "source_type": hit.get("source_type", ""),
                        },
                    })

        return questions, seen_chunks

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        """Heuristic: check if text looks like an interview question."""
        if not text:
            return False
        question_markers = ["?", "？", "如何", "怎么", "请", "什么是", "说说", "谈谈", "解释", "区别", "优缺点", "为什么"]
        return any(marker in text for marker in question_markers)

    async def _llm_generate_questions(
        self,
        structured: dict,
        existing: list[dict],
        missing_dims: list[str],
        count_needed: int,
        target_count: int,
        target_position: str = "",
        interview_mode: str = "comprehensive",
        plan_json: dict | None = None,
        retrieved_context: str = "",
    ) -> list[dict]:
        """Use LLM to generate supplementary questions."""
        import json as _json
        try:
            existing_text = _json.dumps(
                [{"question": q["question"], "dimension": q.get("dimension")}
                 for q in existing],
                ensure_ascii=False,
            )
            prompt = _load_prompt("interview_question_generation_v1.md").format(
                target_position=target_position or "未指定",
                interview_plan=_json.dumps(plan_json or {}, ensure_ascii=False),
                resume_summary=_json.dumps(structured, ensure_ascii=False, indent=2),
                retrieved_context=retrieved_context or "（无）",
                existing_questions=existing_text or "（无）",
                missing_dimensions=", ".join(missing_dims) if missing_dims else "（无）",
                count_needed=str(count_needed),
                target_count=str(target_count),
                interview_mode=interview_mode,
            )
            response = await self.llm.chat([{"role": "user", "content": prompt}])
            items = self._parse_json_response(response)
            for item in items:
                item["source"] = "LLM_GENERATED"
                item["evidence_json"] = None
            return items
        except Exception:
            logger.exception("LLM question generation failed")
            return []

    async def _complete_answers(
        self, questions: list[dict], structured: dict
    ) -> list[dict]:
        """Complete missing standard_answer for VECTOR_RETRIEVED questions."""
        for q in questions:
            if q.get("source") == "VECTOR_RETRIEVED" and not q.get("standard_answer"):
                q["source"] = "HYBRID"
                # For simplicity, leave standard_answer empty and generate on-demand
                # Full implementation would call LLM per question
        return questions

    @staticmethod
    def _interleave_questions(questions: list[dict]) -> list[dict]:
        """Sort questions by interleaving dimensions to avoid consecutive same-dimension."""
        if not questions:
            return questions
        by_dim: dict[str, list[dict]] = {}
        for q in questions:
            dim = q.get("dimension", "综合")
            by_dim.setdefault(dim, []).append(q)

        result: list[dict] = []
        dims = list(by_dim.keys())
        idx = 0
        while any(by_dim[d] for d in dims):
            dim = dims[idx % len(dims)]
            if by_dim[dim]:
                result.append(by_dim[dim].pop(0))
            idx += 1
        return result

    async def _retrieve_kb(
        self, structured: dict | None, current_message: str, recent_messages: list
    ) -> list[dict]:
        """Retrieve KB chunks for follow-up mode."""
        try:
            retrieval_query = self.prompt_builder.build_retrieval_query(
                resume_structured=structured,
                current_message=current_message,
                recent_messages=recent_messages,
            )
            query_vec = await self.embedding.embed_query(retrieval_query)
            chunks = self.vector_store.search(
                "kb_chunks_current",
                query_vec,
                top_k=settings.interview_retrieval_top_k,
            )
            return [
                c for c in chunks
                if c.get("score", 0) >= settings.interview_retrieval_min_score
            ]
        except Exception:
            logger.exception("KB retrieval failed in follow-up mode")
            return []

    @staticmethod
    def _is_follow_up_mode(recent_messages: list) -> bool:
        """Check if the last assistant message was a FOLLOW_UP."""
        for m in reversed(recent_messages):
            meta = getattr(m, "metadata_json", None)
            if isinstance(meta, dict) and meta.get("action") == "FOLLOW_UP":
                return True
            if isinstance(meta, dict) and meta.get("action") in ("NEXT_QUESTION", "COMPLETE"):
                return False
        return False

    @staticmethod
    def _parse_decision(llm_response: str) -> dict:
        """Parse LLM JSON response robustly."""
        import re as _re
        try:
            # Try direct JSON parse
            return json.loads(llm_response.strip())
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            # Try to extract JSON block
            match = _re.search(r'\{[^{}]*\}', llm_response, _re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
        logger.warning("Failed to parse LLM decision, using default")
        return {
            "evaluation": llm_response[:200],
            "score": 3,
            "action": "NEXT_QUESTION",
            "follow_up_question": None,
            "next_question_preview": None,
        }

    @staticmethod
    def _parse_json_response(response: str) -> list[dict]:
        """Parse LLM JSON array response robustly."""
        import re as _re
        try:
            data = json.loads(response.strip())
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            match = _re.search(r'\[[\s\S]*\]', response)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
        logger.warning("Failed to parse LLM JSON array response")
        return []


def _load_prompt(name: str) -> str:
    from pathlib import Path
    _PROMPT_DIR = (
        Path(__file__).parent.parent.parent.parent.parent / "prompt_templates"
    )
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")
