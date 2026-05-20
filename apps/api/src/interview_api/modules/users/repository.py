from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.users.models import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create(self, email: str, password_hash: str, username: str | None = None, role: str = "USER") -> User:
        user = User(
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
        )
        self.db.add(user)
        await self.db.flush()
        return user
