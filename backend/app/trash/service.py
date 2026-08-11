import asyncio
import uuid
from contextlib import suppress
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.files.browser import BrowserPathNotDirectoryError, BrowserPathNotFoundError, FileEntryKind
from app.models import TrashEntry, User
from app.trash.filesystem import TrashFilesystem, TrashFilesystemEntry, TrashStorageMissingError

MAX_TRASH_ENTRIES = 1000


class TrashServiceError(RuntimeError):
    pass


class TrashEntryNotFoundError(TrashServiceError):
    pass


class TrashRestoreTargetMissingError(TrashServiceError):
    pass


class TrashPersistenceError(TrashServiceError):
    pass


class TrashCompensationError(TrashServiceError):
    pass


@dataclass(frozen=True, slots=True)
class TrashListing:
    entries: list[TrashEntry]
    truncated: bool


class TrashService:
    def __init__(self, db: AsyncSession, filesystem: TrashFilesystem) -> None:
        self._db = db
        self._filesystem = filesystem

    async def list_entries(self, user_id: uuid.UUID) -> TrashListing:
        records = list(
            (
                await self._db.scalars(
                    select(TrashEntry)
                    .where(TrashEntry.user_id == user_id)
                    .order_by(TrashEntry.deleted_at.desc(), TrashEntry.id.desc())
                    .limit(MAX_TRASH_ENTRIES + 1)
                )
            ).all()
        )
        return TrashListing(
            entries=records[:MAX_TRASH_ENTRIES],
            truncated=len(records) > MAX_TRASH_ENTRIES,
        )

    async def move_to_trash(self, user: User, raw_path: str) -> TrashEntry:
        # A rollback expires ORM instances. Keep the immutable values required by
        # the filesystem compensation outside SQLAlchemy's object lifecycle.
        username = user.username
        user_id = user.id
        entry_id = uuid.uuid4()
        filesystem_entry = await run_in_threadpool(
            self._filesystem.move_to_trash,
            username,
            user_id,
            entry_id,
            raw_path,
        )
        record = TrashEntry(
            id=filesystem_entry.id,
            user_id=user_id,
            original_path=filesystem_entry.original_path,
            name=filesystem_entry.name,
            kind=filesystem_entry.kind.value,
            size=filesystem_entry.size,
            device=filesystem_entry.device,
            inode=filesystem_entry.inode,
        )
        self._db.add(record)
        try:
            await self._db.commit()
        except BaseException as exc:
            await self._rollback_database()
            try:
                await asyncio.shield(
                    run_in_threadpool(
                        self._filesystem.restore,
                        username,
                        user_id,
                        filesystem_entry,
                    )
                )
            except BaseException as compensation_error:
                raise TrashCompensationError(
                    "Trash metadata failed and the file could not be restored"
                ) from compensation_error
            if isinstance(exc, Exception):
                raise TrashPersistenceError("Trash metadata could not be saved") from exc
            raise
        return record

    async def restore(self, user: User, entry_id: uuid.UUID) -> TrashFilesystemEntry:
        # These values must remain available if commit fails and rollback expires
        # the authenticated user instance before the compensating move.
        username = user.username
        user_id = user.id
        record = await self._locked_entry(user_id, entry_id)
        filesystem_entry = self._filesystem_entry(record)
        try:
            await run_in_threadpool(
                self._filesystem.restore,
                username,
                user_id,
                filesystem_entry,
            )
        except (BrowserPathNotFoundError, BrowserPathNotDirectoryError) as exc:
            raise TrashRestoreTargetMissingError(
                "The original parent directory does not exist"
            ) from exc

        await self._db.delete(record)
        try:
            await self._db.commit()
        except BaseException as exc:
            await self._rollback_database()
            try:
                await asyncio.shield(
                    run_in_threadpool(
                        self._filesystem.restage,
                        username,
                        user_id,
                        filesystem_entry,
                    )
                )
            except BaseException as compensation_error:
                raise TrashCompensationError(
                    "Restore metadata failed and the file could not be returned to trash"
                ) from compensation_error
            if isinstance(exc, Exception):
                raise TrashPersistenceError("Restore metadata could not be removed") from exc
            raise
        return filesystem_entry

    async def purge(self, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        record = await self._locked_entry(user_id, entry_id)
        filesystem_entry = self._filesystem_entry(record)
        with suppress(TrashStorageMissingError):
            await run_in_threadpool(self._filesystem.purge, user_id, filesystem_entry)
        await self._db.delete(record)
        try:
            await self._db.commit()
        except BaseException as exc:
            await self._rollback_database()
            if isinstance(exc, Exception):
                raise TrashPersistenceError(
                    "The file was purged but its metadata could not be removed; retry is safe"
                ) from exc
            raise

    async def _locked_entry(self, user_id: uuid.UUID, entry_id: uuid.UUID) -> TrashEntry:
        record = await self._db.scalar(
            select(TrashEntry)
            .where(TrashEntry.id == entry_id, TrashEntry.user_id == user_id)
            .with_for_update()
        )
        if record is None:
            raise TrashEntryNotFoundError("Trash entry not found")
        return record

    async def _rollback_database(self) -> None:
        with suppress(BaseException):
            await asyncio.shield(self._db.rollback())

    @staticmethod
    def _filesystem_entry(record: TrashEntry) -> TrashFilesystemEntry:
        return TrashFilesystemEntry(
            id=record.id,
            original_path=record.original_path,
            name=record.name,
            kind=FileEntryKind(record.kind),
            size=record.size,
            device=record.device,
            inode=record.inode,
        )
