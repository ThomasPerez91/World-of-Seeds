from collections.abc import AsyncIterator
from dataclasses import dataclass
from math import isfinite

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


@dataclass(frozen=True, slots=True)
class DatabaseTimeouts:
    """Fixed runtime policy; shortened only by isolated fault-injection tests."""

    connect_seconds: float = 5.0
    command_seconds: float = 15.0
    pool_seconds: float = 5.0

    def __post_init__(self) -> None:
        if any(
            not isfinite(value) or not 0 < value <= 30
            for value in (self.connect_seconds, self.command_seconds, self.pool_seconds)
        ):
            raise ValueError("Database deadlines must be finite and within (0, 30]")


RUNTIME_DATABASE_TIMEOUTS = DatabaseTimeouts()


def create_database_engine(
    config: Settings, *, timeouts: DatabaseTimeouts = RUNTIME_DATABASE_TIMEOUTS
) -> AsyncEngine:
    url = config.sqlalchemy_database_url
    if make_url(url).drivername == "postgresql+asyncpg":
        # Pool admission, connection establishment and commands are separate waits.
        # command_timeout also covers pre-ping, transactions and lock waits.
        return create_async_engine(
            url,
            module=BoundedAsyncpgDBAPI(timeouts.command_seconds),
            pool_pre_ping=True,
            pool_timeout=timeouts.pool_seconds,
            connect_args={
                "timeout": timeouts.connect_seconds,
                "command_timeout": timeouts.command_seconds,
            },
        )
    # SQLite is used by isolated tests and does not accept asyncpg options.
    return create_async_engine(url, pool_pre_ping=True)


engine = create_database_engine(settings)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
