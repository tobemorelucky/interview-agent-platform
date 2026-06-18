"""Small permission guard helpers."""

from collections.abc import Callable

from fastapi import Depends

from interview_api.api.deps import get_current_user
from interview_api.core.errors import PermissionDeniedError
from interview_api.modules.users.models import User


ROLE_PERMISSIONS = {
    "USER": {
        "memory:read:self",
        "memory:write:self",
        "interview:read:self",
        "interview:write:self",
    },
    "ADMIN": {
        "admin:access",
        "experience:manage",
        "audit:read",
        "memory:read:any",
        "memory:read:self",
        "memory:write:self",
        "interview:read:self",
        "interview:write:self",
    },
}


def require_roles(*roles: str) -> Callable:
    allowed = {role.upper() for role in roles}

    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.upper() not in allowed:
            raise PermissionDeniedError()
        return current_user

    return dependency


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role.upper() != "ADMIN":
        raise PermissionDeniedError("需要管理员权限")
    return current_user


def ensure_owner_or_admin(resource_user_id: int, current_user: User) -> None:
    if current_user.role.upper() == "ADMIN":
        return
    if int(resource_user_id) != int(current_user.id):
        raise PermissionDeniedError()


def require_permissions(*permissions: str) -> Callable:
    required = set(permissions)

    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        allowed = ROLE_PERMISSIONS.get(current_user.role.upper(), set())
        if not required.issubset(allowed):
            raise PermissionDeniedError()
        return current_user

    return dependency
