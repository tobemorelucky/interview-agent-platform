"""Round 23 / L3: the persistent business fence.

`purge_requests` is the fence itself. Every new-business choke point asks this
module whether a resource has an outstanding (or completed) purge request and
refuses if so. The row exists from the moment a request is accepted, so no
window exists between "user asked" and "resource leaves new business".

COMPATIBILITY: the production database is still at 0025 and has no
`purge_requests` table. `to_regclass` makes the probe a no-op there instead of
raising UndefinedTable in the middle of a normal interview. This is deliberate:
adding L3 must not break a pre-0029 database.
"""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.exceptions import AppError
from .constants import RESUME, PRIVATE_JD
from .models import PurgeRequest


async def table_present(db: AsyncSession) -> bool:
    return bool(await db.scalar(text("select to_regclass('public.purge_requests') is not null")))


async def fence_row(db: AsyncSession, resource_type: str, resource_id: int) -> PurgeRequest | None:
    """Any row is a fence: REQUESTED..COMPLETED, FAILED and BLOCKED included."""
    if not await table_present(db):
        return None
    return await db.scalar(select(PurgeRequest).where(
        PurgeRequest.resource_type == resource_type,
        PurgeRequest.resource_id == resource_id,
    ).limit(1))


async def is_fenced(db: AsyncSession, resource_type: str, resource_id: int | None) -> bool:
    if resource_id is None:
        return False
    return await fence_row(db, resource_type, resource_id) is not None


async def assert_not_fenced(db: AsyncSession, resource_type: str, resource_id: int | None,
                            *, what: str = "该资料") -> None:
    """Raise a stable 409 at the choke point. Never leaks ownership details."""
    if resource_id is None:
        return
    if await is_fenced(db, resource_type, resource_id):
        raise AppError("RESOURCE_PURGED", f"{what}已申请彻底清除，不能用于新的面试、训练或匹配", 409)


async def assert_resume_usable(db: AsyncSession, resume_id: int | None) -> None:
    await assert_not_fenced(db, RESUME, resume_id, what="该简历")


async def assert_target_usable(db: AsyncSession, target_id: int | None) -> None:
    await assert_not_fenced(db, PRIVATE_JD, target_id, what="该目标岗位")


async def assert_profile_version_usable(db: AsyncSession, version_id: int | None) -> None:
    """A direct candidate_profile_version_id must not bypass the resume fence."""
    if version_id is None:
        return
    from interview_api.modules.candidate_profile.models import CandidateProfileVersion
    source = await db.scalar(select(CandidateProfileVersion.source_resume_id).where(
        CandidateProfileVersion.id == version_id))
    await assert_resume_usable(db, source)
