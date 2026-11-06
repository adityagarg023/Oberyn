from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import load_settings
from app.models import Base

settings = load_settings()

url = settings.database_url

if url.startswith("postgresql://"):
    url = url.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1,
    )

engine = create_async_engine(
    url,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

__all__ = ["Base", "SessionLocal", "engine"]
