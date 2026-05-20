from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    UserResponse,
    TokenResponse,
)
from interview_api.modules.auth.service import AuthService
from interview_api.api.deps import get_current_user
from interview_api.modules.users.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    user_id = await service.register(
        email=body.email,
        username=body.username,
        password=body.password,
    )
    return success(data={"user_id": user_id})


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    result = await service.login(email=body.email, password=body.password)
    return success(data=result)


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return success(
        data={
            "id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
            "role": current_user.role,
        }
    )
