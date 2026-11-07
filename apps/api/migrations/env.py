from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL")

    if not url:
        url = config.get_main_option("sqlalchemy.url")

    if url.startswith("postgresql://"):
        url = url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
