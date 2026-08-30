from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

engine = create_async_engine(settings.db_url, pool_pre_ping=True)
sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


async def init_models() -> None:
    """Быстрый старт без Alembic. На проде — миграции."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)