from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.database_driver import BoundedAsyncpgDBAPI

settings = get_settings()


def create_database_engine(config: Settings) -> AsyncEngine:
    url = config.sqlalchemy_database_url
    if make_url(url).drivername == "postgresql+asyncpg":
        # Pool admission, connection establishment and commands are separate waits.
        # command_timeout also covers pre-ping, transactions and lock waits.
        return create_async_engine(
            url,
            module=BoundedAsyncpgDBAPI(config.database_command_timeout_seconds),
            pool_pre_ping=True,
            pool_timeout=config.database_pool_timeout_seconds,
            connect_args={
                "timeout": config.database_connect_timeout_seconds,
                "command_timeout": config.database_command_timeout_seconds,
            },
        )
    # SQLite is used by isolated tests and does not accept asyncpg options.
    return create_async_engine(url, pool_pre_ping=True)


engine = create_database_engine(settings)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
