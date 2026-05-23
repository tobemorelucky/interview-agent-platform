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
                f"我已读取你的简历。根据分析，我判断你面试的目标岗位是 **{pos}**，是否正确？\n\n"
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

        # If resume already has target_role, use it
        if target_role:
            return target_role

        # Build suggestion from skills + current role
        skills = structured.get("skills", {})
        if isinstance(skills, dict):
            tech_keywords = []
            for key in ("languages", "frameworks", "ai_ml"):
                vals = skills.get(key, [])
                if isinstance(vals, list):
                    tech_keywords.extend(vals[:3])

            if tech_keywords:
                tech_str = "/".join(tech_keywords[:3])
                if current_role:
                    return f"{tech_str} {current_role}"
                return f"{tech_str} 开发"

        if current_role:
            return current_role

        # Last resort: construct from projects
        projects = structured.get("projects", [])
        if isinstance(projects, list) and projects:
            for p in projects[:1]:
                if isinstance(p, dict) and p.get("name"):
                    return f"{p['name']} 相关开发"

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
            await chat_service.generate_questions(session_id)
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

    # ── Question Generation ──

    async def generate_questions(self, session_id: int) -> None:
        """Generate interview question queue for a session.

        Runs as a background task (asyncio.create_task).
        Updates question_generation_status throughout.
        """
        try:
            session = await self.session_repo.get_by_id(session_id)
            if session is None or session.resume_id is None:
                return

            resume = await self.resume_repo.get_by_id(session.resume_id)
            if resume is None or resume.status != "COMPLETED":
                await self.session_repo.update_question_generation_status(
                    session_id, "FAILED", "简历未处理完成"
                )
                await self.db.commit()
                return

            report = await self.report_repo.get_by_resume_id(session.resume_id)
            if report is None or not report.summary_json:
                await self.session_repo.update_question_generation_status(
                    session_id, "FAILED", "简历解析报告不存在"
                )
                await self.db.commit()
                return

            structured = report.summary_json
            target_pos = session.target_position or ""
            target_count = session.question_count or settings.interview_question_count

            # Clean old questions
            await self.question_repo.delete_by_session_id(session_id)
            await self.session_repo.update_question_generation_status(
                session_id, "GENERATING"
            )
            await self.db.commit()

            # ── Planner Agent Step tracker ──
            trace_steps: list[dict] = []
            plan_data: dict = {
                "target_position": target_pos,
                "interview_mode": session.interview_mode,
                "question_count": target_count,
            }

            # ── Step 1: ANALYZE_RESUME ──
            analysis_summary = f"识别到 {target_pos} 相关技能"
            trace_steps.append({
                "step": "ANALYZE_RESUME",
                "status": "DONE",
                "summary": analysis_summary,
            })

            # ── Step 2: PLAN_DIMENSIONS ──
            dimensions_raw = await self._extract_dimensions(
                structured, target_position=target_pos, question_count=target_count
            )

            if isinstance(dimensions_raw, dict):
                dimensions = dimensions_raw.get("dimensions", [])
                analysis_summary = dimensions_raw.get("analysis_summary", analysis_summary)
            else:
                dimensions = dimensions_raw if isinstance(dimensions_raw, list) else []

            trace_steps.append({
                "step": "PLAN_DIMENSIONS",
                "status": "DONE",
                "summary": f"生成 {len(dimensions)} 个面试维度，目标 {target_count} 道候选题",
            })
            plan_data["dimensions"] = [
                {
                    "name": d.get("dimension", ""),
                    "weight": d.get("weight", 0),
                    "question_count": d.get("question_count", 0),
                    "reason": d.get("reason", ""),
                }
                for d in dimensions
            ]

            # ── Step 3: RETRIEVE_QUESTIONS ──
            retrieval_service = QuestionRetrievalService(
                self.embedding, self.vector_store
            )
            grouped_hits = await retrieval_service.retrieve_by_dimensions(
                dimensions,
                top_k=settings.interview_question_retrieval_top_k,
                min_score=settings.interview_question_retrieval_min_score,
            )
            total_hits = sum(len(h) for h in grouped_hits.values())
            hit_summary_parts = []
            for dim_name, hits in grouped_hits.items():
                if hits:
                    hit_summary_parts.append(f"{dim_name} 命中 {len(hits)} 条")
            trace_steps.append({
                "step": "RETRIEVE_QUESTIONS",
                "status": "DONE",
                "summary": "; ".join(hit_summary_parts) if hit_summary_parts else "无命中",
            })

            # ── Step 4: JUDGE_SUFFICIENCY ──
            kb_questions, seen_chunks = self._extract_questions_from_hits(grouped_hits)
            all_questions = list(kb_questions)
            vector_count = sum(
                1 for q in all_questions if q.get("source") == "VECTOR_RETRIEVED"
            )

            if len(all_questions) < target_count:
                covered_dims = {q["dimension"] for q in all_questions if q.get("dimension")}
                all_dim_names = {d.get("dimension", "") for d in dimensions}
                missing_dims = list(all_dim_names - covered_dims)
                count_needed = target_count - len(all_questions)

                llm_questions = await self._llm_generate_questions(
                    structured, all_questions, missing_dims, count_needed, target_count,
                    target_position=target_pos,
                    interview_mode=session.interview_mode,
                    plan_json=plan_data,
                    retrieved_context=json.dumps(
                        {k: [{"title": h.get("title", ""), "preview": h.get("preview", "")[:200]}
                             for h in v[:3]]
                         for k, v in grouped_hits.items()},
                        ensure_ascii=False,
                    ),
                )
                all_questions.extend(llm_questions)

            all_questions = await self._complete_answers(all_questions, structured)

            hybrid_count = sum(1 for q in all_questions if q.get("source") == "HYBRID")
            llm_count = sum(1 for q in all_questions if q.get("source") == "LLM_GENERATED")

            plan_data["source_distribution"] = {
                "VECTOR_RETRIEVED": vector_count,
                "HYBRID": hybrid_count,
                "LLM_GENERATED": llm_count,
            }
            trace_steps.append({
                "step": "BUILD_QUESTION_QUEUE",
                "status": "DONE",
                "summary": (
                    f"生成 {len(all_questions)} 道题："
                    f"VECTOR_RETRIEVED={vector_count}, "
                    f"HYBRID={hybrid_count}, "
                    f"LLM_GENERATED={llm_count}"
                ),
            })

            # ── Step 5: Interleave, index, save ──
            all_questions = self._interleave_questions(all_questions)
            for i, q in enumerate(all_questions):
                q["question_index"] = i
                q["planned_order"] = i
                q["status"] = "PENDING"

            if all_questions:
                await self.question_repo.batch_create(session_id, all_questions)

            # Save plan and trace to session
            await self.session_repo.save_plan(
                session_id,
                plan_json=plan_data,
                trace_json={"steps": trace_steps},
            )

            await self.session_repo.update_question_generation_status(
                session_id, "READY"
            )
            await self.db.commit()
            logger.info(
                "Question generation complete: session=%s count=%s plan=%s",
                session_id,
                len(all_questions),
                json.dumps(plan_data.get("source_distribution", {}), ensure_ascii=False),
            )

        except Exception as e:
            logger.exception("Question generation failed for session %s", session_id)
            try:
                await self.session_repo.update_question_generation_status(
                    session_id, "FAILED", str(e)
                )
                await self.db.commit()
            except Exception:
                logger.exception("Failed to update FAILED status")

    # ── Start Interview ──

    async def start_interview(
        self, session_id: int, user_id: int
    ) -> dict | None:
        """Start the interview: return first question."""
        session = await self.session_repo.get_by_id_and_user(session_id, user_id)
        if session is None:
            return None

        resume = await self.resume_repo.get_by_id(session.resume_id)
        if resume is None or resume.status != "COMPLETED":
            return None

        if not session.target_position_confirmed:
            return {
                "error": "TARGET_POSITION_REQUIRED",
                "message": "请先确认本次面试岗位",
            }

        gen_status = session.question_generation_status
        if gen_status == "PENDING":
            return {
                "error": "QUESTIONS_NOT_READY",
                "sub_code": "PENDING",
                "message": "题目尚未生成，请等待简历处理完成或手动触发生成",
            }
        elif gen_status == "GENERATING":
            return {
                "error": "QUESTIONS_NOT_READY",
                "sub_code": "GENERATING",
                "message": "题目正在生成中，请稍候",
            }
        elif gen_status == "FAILED":
            return {
                "error": "QUESTIONS_NOT_READY",
                "sub_code": "FAILED",
                "message": "题目生成失败，请重试 POST /questions/generate",
            }

        questions = await self.question_repo.get_by_session_id(session_id)
        if not questions:
            return {
                "error": "QUESTIONS_NOT_READY",
                "sub_code": "EMPTY",
                "message": "题目列表为空",
            }

        q = questions[0]
        await self.question_repo.update_status(q.id, "ASKED")
        await self.session_repo.update_current_question_index(session_id, 0)

        # Save welcome message
        await self.msg_repo.create(
            session_id=session_id,
            role="ASSISTANT",
            content=f"开始面试。第一题：{q.question}",
            metadata_json={"source": "QUESTION_DRIVEN", "question_id": q.id},
            turn_index=0,
        )
        await self.db.commit()

        return {
            "type": "QUESTION",
            "question_id": q.id,
            "question_index": 0,
            "total_questions": len(questions),
            "question": q.question,
            "dimension": q.dimension,
            "difficulty": q.difficulty,
            "source": q.source,
            "evidence": q.evidence_json,
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

        # Check question readiness
        gen_status = session.question_generation_status
        if gen_status != "READY":
            yield _sse("error", {
                "code": "QUESTIONS_NOT_READY",
                "sub_code": gen_status,
                "message": {
                    "PENDING": "题目尚未生成",
                    "GENERATING": "题目正在生成中，请稍候",
                    "FAILED": f"题目生成失败: {session.question_generation_error or '未知错误'}",
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

            # Stream LLM response
            llm_messages = [{"role": "user", "content": prompt}]
            full_content = ""

            async for token in self.llm.chat_stream(llm_messages):
                full_content += token
                yield _sse("token", {"content": token})

            # Parse LLM decision
            decision = self._parse_decision(full_content)

            action = decision.get("action", "NEXT_QUESTION")
            evaluation_text = decision.get("evaluation", "")
            score = decision.get("score", 0)

            yield _sse("evaluation", {
                "score": score,
                "comment": evaluation_text,
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
                dynamic_q_data = decision.get("dynamic_question") or {}
                if dynamic_q_data.get("question"):
                    new_index = q.question_index + 1
                    new_q = await self.question_repo.create_dynamic(
                        session_id=session_id,
                        question_data={
                            "question_index": new_index,
                            "question": dynamic_q_data["question"],
                            "standard_answer": dynamic_q_data.get("standard_answer"),
                            "dimension": dynamic_q_data.get("dimension"),
                            "difficulty": dynamic_q_data.get("difficulty"),
                            "source": dynamic_q_data.get("source", "LLM_GENERATED"),
                            "parent_question_id": q.id,
                            "planned_order": q.planned_order,
                        },
                    )
                    # Update question count for subsequent questions
                    all_questions = await self.question_repo.get_by_session_id(session_id)
                    yield _sse("dynamic_question", {
                        "question_id": new_q.id,
                        "question_index": new_q.question_index,
                        "question": new_q.question,
                        "source": new_q.source,
                        "dimension": new_q.dimension,
                        "difficulty": new_q.difficulty,
                        "reason": dynamic_q_data.get("reason", ""),
                        "parent_question_id": q.id,
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
                new_index = session.current_question_index + 1
                await self.session_repo.update_current_question_index(session_id, new_index)

                next_q = await self.question_repo.get_by_index(session_id, new_index)
                if next_q:
                    await self.question_repo.update_status(next_q.id, "ASKED")
                    preview = decision.get("next_question_preview", "")
                    yield _sse("question_transition", {
                        "from_index": session.current_question_index,
                        "to_index": new_index,
                        "preview": preview,
                    })
                    yield _sse("question", {
                        "question_id": next_q.id,
                        "question_index": new_index,
                        "total_questions": len(all_questions),
                        "question": next_q.question,
                        "source": next_q.source,
                        "dimension": next_q.dimension,
                        "difficulty": next_q.difficulty,
                        "evidence": next_q.evidence_json,
                    })
                else:
                    action = "COMPLETE"

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

            # Save assistant message
            assistant_content = evaluation_text
            if action == "FOLLOW_UP":
                assistant_content += "\n\n" + decision.get("follow_up_question", "")
            elif action == "INSERT_DYNAMIC_QUESTION":
                dq = decision.get("dynamic_question") or {}
                assistant_content += (
                    "\n\n" + dq.get("question", "")
                    + "\n\n（基于你的回答临场追问）"
                )
            await self.msg_repo.create(
                session_id=session_id,
                role="ASSISTANT",
                content=assistant_content,
                metadata_json={
                    "source": "QUESTION_DRIVEN",
                    "question_id": q.id,
                    "action": action,
                    "score": score,
                    "is_follow_up": is_follow_up,
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
                from sqlalchemy import update
                await self.db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == session.id)
                    .values(target_position_confirmed=True)
                )
                await self.db.flush()

                confirm_msg = (
                    f"好的，确认面试岗位：**{suggested}**。\n\n"
                    "正在为你生成面试题，请稍候..."
                )
                yield _sse("token", {"content": confirm_msg})

                await self.msg_repo.create(
                    session_id=session.id,
                    role="ASSISTANT",
                    content=confirm_msg,
                    metadata_json={
                        "source": "SYSTEM",
                        "type": "POSITION_CONFIRMED",
                        "target_position": suggested,
                    },
                    turn_index=turn_index,
                )
                await self.session_repo.increment_turn(session.id)
                await self.db.commit()

                yield _sse("evaluation", {
                    "score": 0,
                    "comment": f"岗位确认：{suggested}",
                    "action": "POSITION_CONFIRMED",
                })
                yield _sse("done", {
                    "message_id": 0,
                    "turn_index": turn_index,
                    "action": "POSITION_CONFIRMED",
                })
                return

            elif result.startswith("NEW_POSITION:"):
                new_pos = result.replace("NEW_POSITION:", "").strip()
                await self.session_repo.update_target_position(
                    session.id,
                    target_position=new_pos,
                    interview_mode=session.interview_mode,
                    question_count=session.question_count,
                )
                from sqlalchemy import update
                await self.db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == session.id)
                    .values(target_position_confirmed=True)
                )
                await self.db.flush()

                confirm_msg = (
                    f"好的，更新面试岗位为：**{new_pos}**。\n\n"
                    "正在为你生成面试题，请稍候..."
                )
                yield _sse("token", {"content": confirm_msg})

                await self.msg_repo.create(
                    session_id=session.id,
                    role="ASSISTANT",
                    content=confirm_msg,
                    metadata_json={
                        "source": "SYSTEM",
                        "type": "POSITION_CONFIRMED",
                        "target_position": new_pos,
                    },
                    turn_index=turn_index,
                )
                await self.session_repo.increment_turn(session.id)
                await self.db.commit()

                yield _sse("evaluation", {
                    "score": 0,
                    "comment": f"岗位已更新：{new_pos}",
                    "action": "POSITION_CONFIRMED",
                })
                yield _sse("done", {
                    "message_id": 0,
                    "turn_index": turn_index,
                    "action": "POSITION_CONFIRMED",
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
