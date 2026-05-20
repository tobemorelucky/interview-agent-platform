from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from interview_api.core.exceptions import (
    AuthInvalidCredentialsError,
    DuplicateEmailError,
    DuplicateUsernameError,
)
from interview_api.modules.users.repository import UserRepository


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def register(
        self, email: str, username: str, password: str, role: str = "USER"
    ) -> int:
        existing = await self.repo.get_by_email(email)
        if existing:
            raise DuplicateEmailError()

        if username:
            existing_username = await self.repo.get_by_username(username)
            if existing_username:
                raise DuplicateUsernameError()

        password_hash = hash_password(password)
        user = await self.repo.create(
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
        )
        return user.id

    async def login(self, email: str, password: str) -> dict:
        user = await self.repo.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise AuthInvalidCredentialsError()

        if not user.is_active:
            raise AuthInvalidCredentialsError()

        access_token = create_access_token(user_id=user.id, role=user.role)
        return {"access_token": access_token, "token_type": "bearer"}

    async def get_me(self, user_id: int) -> dict:
        user = await self.repo.get_by_id(user_id)
        return {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "role": user.role,
        }
