from datetime import datetime, timezone

from sqlalchemy import select, update, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.interview.models import InterviewSession, InterviewMessage


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
