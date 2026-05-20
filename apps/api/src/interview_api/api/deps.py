from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.exceptions import (
    AuthMissingTokenError,
    AuthPermissionDeniedError,
    AuthTokenExpiredError,
)
from interview_api.core.security import decode_access_token
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.users.models import User
from interview_api.modules.users.repository import UserRepository

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthMissingTokenError()

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise AuthTokenExpiredError()

    user_id = payload.get("sub")
    if not user_id:
        raise AuthTokenExpiredError()

    repo = UserRepository(db)
    user = await repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise AuthTokenExpiredError()

    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "ADMIN":
        raise AuthPermissionDeniedError()
    return current_user
