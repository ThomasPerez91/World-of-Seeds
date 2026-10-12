from collections.abc import AsyncIterator
from dataclasses import dataclass
from math import isfinite

from sqlalchemy import event
from sqlalchemy.engine import ExceptionContext, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.database_driver import BoundedAsyncpgDBAPI
from app.core.database_errors import DatabaseUnavailableError, is_database_unavailable

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
        runtime_engine = create_async_engine(
            url,
            module=BoundedAsyncpgDBAPI(timeouts.command_seconds),
            pool_pre_ping=True,
            pool_timeout=timeouts.pool_seconds,
            connect_args={
                "timeout": timeouts.connect_seconds,
                "command_timeout": timeouts.command_seconds,
            },
        )

        @event.listens_for(runtime_engine.sync_engine, "handle_error", retval=True)
        def classify_driver_error(context: ExceptionContext) -> BaseException | None:
            error = context.sqlalchemy_exception or context.original_exception
            if not isinstance(error, Exception):
                # SQLAlchemy also marks task cancellation as a disconnect.
                # Preserve control-flow exceptions while it cleans the connection.
                return None
            if context.is_disconnect or is_database_unavailable(error):
                # The normalized SQLAlchemyError is not a DBAPI exception, so
                # SQLAlchemy cannot infer disconnect from its type alone.
                context.is_disconnect = True  # type: ignore[misc]
                if isinstance(error, DatabaseUnavailableError):
                    # A deadline makes this connection uncertain, not every
                    # otherwise healthy connection already in the pool.
                    context.invalidate_pool_on_disconnect = False  # type: ignore[misc]
                return DatabaseUnavailableError()
            return None

        return runtime_engine
    # SQLite is used by isolated tests and does not accept asyncpg options.
    return create_async_engine(url, pool_pre_ping=True)


engine = create_database_engine(settings)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
