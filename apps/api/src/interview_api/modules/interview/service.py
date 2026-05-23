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

        # Auto-generate welcome message from assistant
        existing_messages = await self.msg_repo.get_by_session_id(session_id)
        has_welcome = any(
            m.role == "ASSISTANT" and m.turn_index == 0 for m in existing_messages
        )
        if not has_welcome:
            welcome = (
                "我已读取你的简历，可以开始模拟面试。\n\n"
                "你可以点击「开始面试」让我根据你的简历进行针对性提问。"
            )
            await self.msg_repo.create(
                session_id=session_id,
                role="ASSISTANT",
                content=welcome,
                metadata_json={"source": "LLM_GENERATED"},
                turn_index=0,
            )

        if session.title is None:
            await self.session_repo.update_title(
                session_id, f"面试练习 - {resume.filename}"
            )

        # Set status to GENERATING, commit, then fire background task.
        await self.session_repo.update_question_generation_status(
            session_id, "GENERATING"
        )
        await self.db.commit()

        # Fire background task with a NEW DB session so it survives
        # this request's session lifecycle.
        asyncio.create_task(
            _generate_questions_background(
                session_id=session_id,
                llm=self.llm,
                embedding=self.embedding,
                vector_store=self.vector_store,
                prompt_builder=self.prompt_builder,
                memory_manager=self.memory_manager,
            )
        )

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

            # Clean old questions
            await self.question_repo.delete_by_session_id(session_id)
            await self.session_repo.update_question_generation_status(
                session_id, "GENERATING"
            )
            await self.db.commit()

            # Step 1: Extract dimensions
            dimensions = await self._extract_dimensions(structured)

            # Step 2: Retrieve from KB
            retrieval_service = QuestionRetrievalService(
                self.embedding, self.vector_store
            )
            grouped_hits = await retrieval_service.retrieve_by_dimensions(
                dimensions,
                top_k=settings.interview_question_retrieval_top_k,
                min_score=settings.interview_question_retrieval_min_score,
            )

            # Step 3: Aggregate and extract questions from KB hits
            kb_questions, seen_chunks = self._extract_questions_from_hits(grouped_hits)

            # Step 4: LLM supplement if not enough
            target_count = settings.interview_question_count
            all_questions = list(kb_questions)

            if len(all_questions) < target_count:
                covered_dims = {q["dimension"] for q in all_questions if q.get("dimension")}
                all_dim_names = {d.get("dimension", "") for d in dimensions}
                missing_dims = list(all_dim_names - covered_dims)
                count_needed = target_count - len(all_questions)

                llm_questions = await self._llm_generate_questions(
                    structured, all_questions, missing_dims, count_needed, target_count
                )
                all_questions.extend(llm_questions)

            # Step 5: Complete standard_answer for VECTOR_RETRIEVED questions
            all_questions = await self._complete_answers(all_questions, structured)

            # Step 6: Assign question_index with dimension interleaving
            all_questions = self._interleave_questions(all_questions)

            for i, q in enumerate(all_questions):
                q["question_index"] = i
                q["status"] = "PENDING"

            # Step 7: Save
            if all_questions:
                await self.question_repo.batch_create(session_id, all_questions)

            await self.session_repo.update_question_generation_status(
                session_id, "READY"
            )
            await self.db.commit()
            logger.info(
                "Question generation complete: session=%s count=%s",
                session_id,
                len(all_questions),
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
                # Check max follow-ups
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

            if action == "NEXT_QUESTION":
                await self.question_repo.update_status(q.id, "ANSWERED")
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
                await self.question_repo.update_status(q.id, "ANSWERED")
                answered = [
                    x for x in all_questions
                    if x.status in ("ANSWERED", "ASKED")
                ]
                avg_score = score  # simplified; real impl would track per-question scores
                yield _sse("interview_complete", {
                    "total_questions": len(all_questions),
                    "answered_count": len(answered),
                    "avg_score": avg_score,
                })

            # Save assistant message
            assistant_content = evaluation_text
            if action == "FOLLOW_UP":
                assistant_content += "\n\n" + decision.get("follow_up_question", "")
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

    async def _extract_dimensions(self, structured: dict) -> list[dict]:
        """Extract interview dimensions from structured resume via LLM."""
        if not settings.interview_dimension_extraction_enabled:
            return self._rule_based_dimensions(structured)

        try:
            prompt = self.prompt_builder.build_dimension_extraction_prompt(structured)
            response = await self.llm.chat([{"role": "user", "content": prompt}])
            return self._parse_json_response(response)
        except Exception:
            logger.exception("Dimension extraction via LLM failed, falling back to rules")
            return self._rule_based_dimensions(structured)

    def _rule_based_dimensions(self, structured: dict) -> list[dict]:
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
                resume_summary=_json.dumps(structured, ensure_ascii=False, indent=2),
                existing_questions=existing_text or "（无）",
                missing_dimensions=", ".join(missing_dims) if missing_dims else "（无）",
                count_needed=str(count_needed),
                target_count=str(target_count),
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
