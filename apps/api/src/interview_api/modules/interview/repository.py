from datetime import datetime, timezone

from sqlalchemy import select, update, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.interview.models import (
    InterviewSession,
    InterviewSessionQuestion,
    InterviewMessage,
)


class InterviewSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: int,
        title: str | None = None,
        resume_id: int | None = None,
    ) -> InterviewSession:
        session = InterviewSession(
            user_id=user_id,
            title=title,
            resume_id=resume_id,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_by_id(self, session_id: int) -> InterviewSession | None:
        result = await self.db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_and_user(
        self, session_id: int, user_id: int
    ) -> InterviewSession | None:
        result = await self.db.execute(
            select(InterviewSession).where(
                InterviewSession.id == session_id,
                InterviewSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: int) -> list[InterviewSession]:
        result = await self.db.execute(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(desc(InterviewSession.updated_at))
        )
        return list(result.scalars().all())

    async def bind_resume(self, session_id: int, resume_id: int) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(resume_id=resume_id, updated_at=datetime.now(timezone.utc))
        )
        await self.db.flush()

    async def update_title(self, session_id: int, title: str) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )
        await self.db.flush()

    async def increment_turn(self, session_id: int) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(
                turn_count=InterviewSession.turn_count + 1,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def update_memory_summary(
        self, session_id: int, summary: str, last_compressed_turn: int
    ) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(
                memory_summary=summary,
                last_compressed_turn=last_compressed_turn,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def delete(self, session_id: int) -> None:
        await self.db.execute(
            delete(InterviewSession).where(InterviewSession.id == session_id)
        )
        await self.db.flush()

    async def update_question_generation_status(
        self, session_id: int, status: str, error: str | None = None
    ) -> None:
        values = {
            "question_generation_status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if error is not None:
            values["question_generation_error"] = error
        elif status == "READY":
            values["question_generation_error"] = None
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(**values)
        )
        await self.db.flush()

    async def update_current_question_index(
        self, session_id: int, index: int
    ) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(
                current_question_index=index,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def update_target_position(
        self,
        session_id: int,
        target_position: str,
        interview_mode: str = "comprehensive",
        question_count: int = 20,
    ) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(
                target_position=target_position,
                target_position_confirmed=True,
                interview_mode=interview_mode,
                question_count=question_count,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def save_plan(
        self,
        session_id: int,
        plan_json: dict,
        trace_json: dict,
    ) -> None:
        await self.db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(
                interview_plan_json=plan_json,
                planner_trace_json=trace_json,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()


class InterviewSessionQuestionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def batch_create(
        self, session_id: int, questions: list[dict]
    ) -> list[InterviewSessionQuestion]:
        objs = [
            InterviewSessionQuestion(
                session_id=session_id,
                question_index=q["question_index"],
                question=q["question"],
                standard_answer=q.get("standard_answer"),
                dimension=q.get("dimension"),
                difficulty=q.get("difficulty"),
                source=q.get("source", "LLM_GENERATED"),
                evidence_json=q.get("evidence_json"),
                status=q.get("status", "PENDING"),
            )
            for q in questions
        ]
        self.db.add_all(objs)
        await self.db.flush()
        return objs

    async def get_by_session_id(
        self, session_id: int
    ) -> list[InterviewSessionQuestion]:
        result = await self.db.execute(
            select(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.session_id == session_id)
            .order_by(InterviewSessionQuestion.question_index)
        )
        return list(result.scalars().all())

    async def get_by_index(
        self, session_id: int, question_index: int
    ) -> InterviewSessionQuestion | None:
        result = await self.db.execute(
            select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == session_id,
                InterviewSessionQuestion.question_index == question_index,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(
        self, question_id: int
    ) -> InterviewSessionQuestion | None:
        result = await self.db.execute(
            select(InterviewSessionQuestion).where(
                InterviewSessionQuestion.id == question_id
            )
        )
        return result.scalar_one_or_none()

    async def update_status(self, question_id: int, status: str) -> None:
        await self.db.execute(
            update(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.id == question_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await self.db.flush()

    async def increment_follow_up(self, question_id: int) -> None:
        await self.db.execute(
            update(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.id == question_id)
            .values(
                follow_up_count=InterviewSessionQuestion.follow_up_count + 1,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def delete_by_session_id(self, session_id: int) -> None:
        await self.db.execute(
            delete(InterviewSessionQuestion).where(
                InterviewSessionQuestion.session_id == session_id
            )
        )
        await self.db.flush()

    async def create_dynamic(
        self,
        session_id: int,
        question_data: dict,
    ) -> InterviewSessionQuestion:
        q = InterviewSessionQuestion(
            session_id=session_id,
            question_index=question_data["question_index"],
            question=question_data["question"],
            standard_answer=question_data.get("standard_answer"),
            dimension=question_data.get("dimension"),
            difficulty=question_data.get("difficulty"),
            source=question_data.get("source", "LLM_GENERATED"),
            evidence_json=question_data.get("evidence_json"),
            parent_question_id=question_data.get("parent_question_id"),
            is_dynamic=True,
            planned_order=question_data.get("planned_order"),
            status="ASKED",
        )
        self.db.add(q)
        await self.db.flush()
        return q

    async def update_evaluation(
        self,
        question_id: int,
        answer_summary: str | None = None,
        missing_points_json: dict | None = None,
        evaluation_json: dict | None = None,
        status: str = "ANSWERED",
    ) -> None:
        values = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if answer_summary is not None:
            values["answer_summary"] = answer_summary
        if missing_points_json is not None:
            values["missing_points_json"] = missing_points_json
        if evaluation_json is not None:
            values["evaluation_json"] = evaluation_json
        await self.db.execute(
            update(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.id == question_id)
            .values(**values)
        )
        await self.db.flush()

    async def get_question_summaries(
        self, session_id: int
    ) -> list[dict]:
        """Return brief summaries (no standard_answer) for all questions."""
        result = await self.db.execute(
            select(InterviewSessionQuestion)
            .where(InterviewSessionQuestion.session_id == session_id)
            .order_by(InterviewSessionQuestion.question_index)
        )
        questions = result.scalars().all()
        return [
            {
                "id": q.id,
                "question_index": q.question_index,
                "question": q.question,
                "dimension": q.dimension,
                "difficulty": q.difficulty,
                "status": q.status,
                "is_dynamic": q.is_dynamic,
                "answer_summary": q.answer_summary,
                "evaluation_json": q.evaluation_json,
            }
            for q in questions
        ]


class InterviewMessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        session_id: int,
        role: str,
        content: str,
        metadata_json: dict | None = None,
        turn_index: int = 0,
    ) -> InterviewMessage:
        msg = InterviewMessage(
            session_id=session_id,
            role=role,
            content=content,
            metadata_json=metadata_json,
            turn_index=turn_index,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def get_by_session_id(self, session_id: int) -> list[InterviewMessage]:
        result = await self.db.execute(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.turn_index)
        )
        return list(result.scalars().all())

    async def get_recent_messages(
        self, session_id: int, limit: int
    ) -> list[InterviewMessage]:
        result = await self.db.execute(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(desc(InterviewMessage.turn_index))
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def get_messages_in_turn_range(
        self, session_id: int, start_turn: int, end_turn: int
    ) -> list[InterviewMessage]:
        result = await self.db.execute(
            select(InterviewMessage)
            .where(
                InterviewMessage.session_id == session_id,
                InterviewMessage.turn_index >= start_turn,
                InterviewMessage.turn_index < end_turn,
            )
            .order_by(InterviewMessage.turn_index)
        )
        return list(result.scalars().all())
