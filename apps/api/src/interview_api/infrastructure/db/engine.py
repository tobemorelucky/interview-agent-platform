from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from interview_api.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_size=10,
    max_overflow=20,
)
