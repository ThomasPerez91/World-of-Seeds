from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    StoragePressureState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.models.base import utc_now
from app.options.registry import OptionValue

ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)


class StorageAdmissionError(RuntimeError):
    """A bounded quota or disk-pressure rejection safe for durable job/API codes."""

    def __init__(self, code: str, pressure: StoragePressureState) -> None:
        super().__init__(code)
        self.code = code
        self.pressure = pressure


@dataclass(frozen=True, slots=True)
class StorageDiskSnapshot:
    total_bytes: int
    free_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.total_bytes) is not int
            or type(self.free_bytes) is not int
            or self.total_bytes <= 0
            or not 0 <= self.free_bytes <= self.total_bytes
        ):
            raise ValueError("storage disk snapshot is invalid")


@dataclass(frozen=True, slots=True)
class StorageAdmissionPolicy:
    managed_max_bytes: int
    user_max_bytes: int
    min_free_bytes: int
    min_free_percent: int
    warning_percent: int
    critical_percent: int

    def __post_init__(self) -> None:
        values = (
            self.managed_max_bytes,
            self.user_max_bytes,
            self.min_free_bytes,
            self.min_free_percent,
            self.warning_percent,
            self.critical_percent,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("storage admission policy is invalid")
        if not 0 <= self.min_free_percent <= 90:
            raise ValueError("storage free-percent policy is invalid")
        if not 1 <= self.warning_percent < self.critical_percent <= 99:
            raise ValueError("storage pressure thresholds are invalid")

    @classmethod
    def from_options(cls, values: Mapping[str, OptionValue]) -> StorageAdmissionPolicy:
        def integer(key: str) -> int:
            value = values.get(key)
            if type(value) is not int:
                raise ValueError("storage option snapshot is incomplete")
            return value

        return cls(
            managed_max_bytes=integer("WOS_STORAGE_MANAGED_MAX_BYTES"),
            user_max_bytes=integer("WOS_STORAGE_USER_MAX_BYTES"),
            min_free_bytes=integer("WOS_STORAGE_MIN_FREE_BYTES"),
            min_free_percent=integer("WOS_STORAGE_MIN_FREE_PERCENT"),
            warning_percent=integer("WOS_STORAGE_PRESSURE_WARNING_PERCENT"),
            critical_percent=integer("WOS_STORAGE_PRESSURE_CRITICAL_PERCENT"),
        )


@dataclass(slots=True)
class StorageAccountingContext:
    usage: UserStorageUsage
    ledger: StorageLedger
    pressure: StoragePressureState


@dataclass(frozen=True, slots=True)
class StorageReconcileResult:
    processed_users: int
    next_user_id: uuid.UUID | None
    completed: bool
    managed_bytes: int
    pressure: StoragePressureState


async def prepare_storage_accounting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    info_hash: str,
    total_size: int,
    policy: StorageAdmissionPolicy | None,
    disk: StorageDiskSnapshot | None,
    now: datetime,
    physical_content_missing: bool | None = None,
) -> StorageAccountingContext:
    usage = await _usage_row(session, user_id, now=now)
    ledger = await _ledger_row(session, now=now)
    existing_torrent = await session.scalar(
        select(ManagedTorrent).where(ManagedTorrent.info_hash == info_hash).with_for_update()
    )
    existing_request = None
    if existing_torrent is not None:
        existing_request = await session.scalar(
            select(TorrentRequest.id).where(
                TorrentRequest.user_id == user_id,
                TorrentRequest.managed_torrent_id == existing_torrent.id,
                TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
            )
        )

    if physical_content_missing is None:
        physical_content_missing = (
            existing_torrent is None or existing_torrent.state is ManagedTorrentState.PURGED
        )
    pressure = ledger.pressure
    if disk is not None:
        projected_free = disk.free_bytes - (total_size if physical_content_missing else 0)
        pressure = classify_storage_pressure(
            StorageDiskSnapshot(disk.total_bytes, max(0, projected_free)),
            policy=_require_policy(policy),
        )
        ledger.disk_total_bytes = disk.total_bytes
        ledger.disk_free_bytes = disk.free_bytes
        ledger.pressure = pressure
        ledger.updated_at = now

    if policy is not None:
        if (
            existing_request is None
            and policy.user_max_bytes
            and usage.logical_bytes + total_size > policy.user_max_bytes
        ):
            raise StorageAdmissionError("user_quota_exceeded", pressure)
        if physical_content_missing:
            if (
                policy.managed_max_bytes
                and ledger.managed_bytes + total_size > policy.managed_max_bytes
            ):
                raise StorageAdmissionError("managed_quota_exceeded", pressure)
            if disk is None:
                raise ValueError("disk snapshot is required for storage admission")
            if pressure is StoragePressureState.CRITICAL:
                raise StorageAdmissionError("disk_pressure_critical", pressure)

    return StorageAccountingContext(
        usage=usage,
        ledger=ledger,
        pressure=pressure,
    )


def apply_storage_accounting(
    context: StorageAccountingContext,
    *,
    request_created: bool,
    managed_torrent_created: bool,
    total_size: int,
    now: datetime,
) -> None:
    if request_created:
        context.usage.logical_bytes += total_size
        context.usage.updated_at = now
    if managed_torrent_created:
        context.ledger.managed_bytes += total_size
        context.ledger.updated_at = now


def classify_storage_pressure(
    snapshot: StorageDiskSnapshot,
    *,
    policy: StorageAdmissionPolicy,
) -> StoragePressureState:
    used_percent = ((snapshot.total_bytes - snapshot.free_bytes) * 100) // snapshot.total_bytes
    free_percent = (snapshot.free_bytes * 100) // snapshot.total_bytes
    if (
        used_percent >= policy.critical_percent
        or snapshot.free_bytes < policy.min_free_bytes
        or free_percent < policy.min_free_percent
    ):
        return StoragePressureState.CRITICAL
    if used_percent >= policy.warning_percent:
        return StoragePressureState.WARNING
    return StoragePressureState.NORMAL


async def reconcile_storage_counters(
    session: AsyncSession,
    *,
    policy: StorageAdmissionPolicy,
    disk: StorageDiskSnapshot,
    batch_size: int = 100,
    after_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StorageReconcileResult:
    if not 1 <= batch_size <= 500:
        raise ValueError("storage reconciliation batch size is invalid")
    timestamp = now or utc_now()
    statement = select(User.id).order_by(User.id).limit(batch_size + 1).with_for_update()
    if after_user_id is not None:
        statement = statement.where(User.id > after_user_id)
    user_ids = list((await session.scalars(statement)).all())
    has_more = len(user_ids) > batch_size
    selected = user_ids[:batch_size]
    if selected:
        logical_bytes_by_user = {
            user_id: int(logical_bytes)
            for user_id, logical_bytes in (
                await session.execute(
                    select(
                        TorrentRequest.user_id,
                        func.coalesce(func.sum(ManagedTorrent.total_size), 0),
                    )
                    .select_from(TorrentRequest)
                    .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
                    .where(
                        TorrentRequest.user_id.in_(selected),
                        TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                    )
                    .group_by(TorrentRequest.user_id)
                )
            ).all()
        }
        usage_by_user = {
            usage.user_id: usage
            for usage in (
                await session.scalars(
                    select(UserStorageUsage)
                    .where(UserStorageUsage.user_id.in_(selected))
                    .with_for_update()
                )
            ).all()
        }
        for user_id in selected:
            usage = usage_by_user.get(user_id)
            if usage is None:
                usage = UserStorageUsage(user_id=user_id)
                session.add(usage)
            usage.logical_bytes = logical_bytes_by_user.get(user_id, 0)
            usage.updated_at = timestamp

    managed_bytes = await session.scalar(
        select(func.coalesce(func.sum(ManagedTorrent.total_size), 0)).where(
            ManagedTorrent.state != ManagedTorrentState.PURGED
        )
    )
    ledger = await _ledger_row(session, now=timestamp)
    ledger.managed_bytes = int(managed_bytes or 0)
    ledger.disk_total_bytes = disk.total_bytes
    ledger.disk_free_bytes = disk.free_bytes
    ledger.pressure = classify_storage_pressure(disk, policy=policy)
    ledger.updated_at = timestamp
    await session.flush()
    return StorageReconcileResult(
        processed_users=len(selected),
        next_user_id=selected[-1] if has_more and selected else None,
        completed=not has_more,
        managed_bytes=ledger.managed_bytes,
        pressure=ledger.pressure,
    )


async def _usage_row(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime,
) -> UserStorageUsage:
    usage = await session.get(UserStorageUsage, user_id, with_for_update=True)
    if usage is None:
        usage = UserStorageUsage(user_id=user_id, logical_bytes=0, updated_at=now)
        session.add(usage)
        await session.flush()
    return usage


async def _ledger_row(session: AsyncSession, *, now: datetime) -> StorageLedger:
    ledger = await session.get(StorageLedger, 1, with_for_update=True)
    if ledger is None:
        ledger = StorageLedger(id=1, updated_at=now)
        session.add(ledger)
        await session.flush()
    return ledger


def _require_policy(policy: StorageAdmissionPolicy | None) -> StorageAdmissionPolicy:
    if policy is None:
        raise ValueError("storage admission policy is required with a disk snapshot")
    return policy
