"""Transient database failures, classified only at the SQL driver boundary."""

from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError


class DatabaseUnavailableError(SQLAlchemyError):
    def __init__(self) -> None:
        super().__init__("database_unavailable")


def is_database_unavailable(error: BaseException) -> bool:
    if isinstance(error, (DatabaseUnavailableError, PoolTimeoutError)):
        return True
    if isinstance(error, DBAPIError):
        if error.connection_invalidated:
            return True
        if error.orig is None:
            return False
        error = error.orig
    state = getattr(error, "sqlstate", None)
    return isinstance(state, str) and (
        state.startswith("08") or state in {"57P01", "57P02", "57P03", "53300"}
    )
