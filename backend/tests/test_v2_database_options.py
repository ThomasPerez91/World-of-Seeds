from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatabaseOption, DatabaseOptionAudit, User
from app.options import (
    OPTION_SPECS,
    DatabaseOptionsDriftError,
    OptionsValidationError,
    PostgresOptionsRegistry,
)

NOW = datetime(2026, 8, 21, 14, tzinfo=UTC)


async def create_user(
    session: AsyncSession,
    username: str,
    *,
    is_admin: bool,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash="test-password-hash",
        is_admin=is_admin,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_database_registry_bootstraps_typed_defaults_and_audit(
    db_session: AsyncSession,
) -> None:
    registry = PostgresOptionsRegistry()
    await registry.initialize(db_session, now=NOW)
    await registry.initialize(db_session, now=NOW)
    await db_session.commit()

    values = await registry.snapshot(db_session)
    option_count = await db_session.scalar(select(func.count()).select_from(DatabaseOption))
    audit_count = await db_session.scalar(select(func.count()).select_from(DatabaseOptionAudit))
    speed = await db_session.get(
        DatabaseOption,
        "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
    )

    assert values == {spec.key: spec.default for spec in OPTION_SPECS}
    assert option_count == audit_count == len(OPTION_SPECS)
    assert speed is not None
    assert speed.value_type == "integer"
    assert speed.minimum_value == 0
    assert speed.maximum_value == 1_000_000_000
    assert speed.version == 1
    assert speed.updated_by_user_id is None


@pytest.mark.asyncio
async def test_admin_update_increments_version_and_records_actor(
    db_session: AsyncSession,
) -> None:
    registry = PostgresOptionsRegistry()
    admin = await create_user(db_session, "options-admin", is_admin=True)
    await registry.initialize(db_session, now=NOW)
    await db_session.commit()

    result = await registry.update(
        db_session,
        {
            "WOS_TORRENT_MAX_ACTIVE_PER_USER": 8,
            "WOS_WORKER_CONCURRENCY": 4,
        },
        actor_user_id=admin.id,
        now=NOW,
    )
    await db_session.commit()

    assert result.changed_keys == (
        "WOS_TORRENT_MAX_ACTIVE_PER_USER",
        "WOS_WORKER_CONCURRENCY",
    )
    assert result.versions == {
        "WOS_TORRENT_MAX_ACTIVE_PER_USER": 2,
        "WOS_WORKER_CONCURRENCY": 2,
    }
    assert result.restart_required is True
    worker = await db_session.get(DatabaseOption, "WOS_WORKER_CONCURRENCY")
    assert worker is not None
    assert worker.value == 4
    assert worker.updated_by_user_id == admin.id
    audits = list(
        (
            await db_session.scalars(
                select(DatabaseOptionAudit)
                .where(DatabaseOptionAudit.option_key == "WOS_WORKER_CONCURRENCY")
                .order_by(DatabaseOptionAudit.version)
            )
        ).all()
    )
    assert [(audit.version, audit.old_value, audit.new_value) for audit in audits] == [
        (1, None, 2),
        (2, 2, 4),
    ]
    assert audits[1].actor_user_id == admin.id
    assert audits[1].change_source == "admin"


@pytest.mark.asyncio
async def test_noop_update_does_not_create_an_audit_event(db_session: AsyncSession) -> None:
    registry = PostgresOptionsRegistry()
    admin = await create_user(db_session, "noop-admin", is_admin=True)
    await registry.initialize(db_session)
    await db_session.commit()
    before = await db_session.scalar(select(func.count()).select_from(DatabaseOptionAudit))

    result = await registry.update(
        db_session,
        {"WOS_TORRENT_MAX_ACTIVE_PER_USER": 5},
        actor_user_id=admin.id,
    )
    await db_session.commit()

    after = await db_session.scalar(select(func.count()).select_from(DatabaseOptionAudit))
    assert result.changed_keys == ()
    assert result.versions == {}
    assert result.restart_required is False
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"WOS_TORRENT_MAX_ACTIVE_PER_USER": True}, "invalid_option"),
        ({"WOS_TORRENT_MAX_ACTIVE_PER_USER": 101}, "invalid_option"),
        ({"WOS_DATABASE_PASSWORD": "not-a-secret"}, "secret_option_forbidden"),
        ({"WOS_UNKNOWN_OPTION": 1}, "unknown_option"),
        (
            {
                "WOS_STORAGE_PRESSURE_WARNING_PERCENT": 95,
                "WOS_STORAGE_PRESSURE_CRITICAL_PERCENT": 90,
            },
            "inconsistent_options",
        ),
    ],
)
async def test_invalid_changes_are_rejected_without_audit(
    db_session: AsyncSession,
    changes: dict[str, bool | int | str],
    code: str,
) -> None:
    registry = PostgresOptionsRegistry()
    admin = await create_user(db_session, "validation-admin", is_admin=True)
    await registry.initialize(db_session)
    await db_session.commit()
    before = await db_session.scalar(select(func.count()).select_from(DatabaseOptionAudit))

    with pytest.raises(OptionsValidationError) as caught:
        await registry.update(db_session, changes, actor_user_id=admin.id)

    after = await db_session.scalar(select(func.count()).select_from(DatabaseOptionAudit))
    assert caught.value.code == code
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_admin", "is_active"),
    [(False, True), (True, False)],
)
async def test_update_requires_an_active_admin_actor(
    db_session: AsyncSession,
    is_admin: bool,
    is_active: bool,
) -> None:
    registry = PostgresOptionsRegistry()
    actor = await create_user(
        db_session,
        f"actor-{is_admin}-{is_active}",
        is_admin=is_admin,
        is_active=is_active,
    )
    await registry.initialize(db_session)

    with pytest.raises(OptionsValidationError) as caught:
        await registry.update(
            db_session,
            {"WOS_TORRENT_MAX_ACTIVE_PER_USER": 6},
            actor_user_id=actor.id,
        )

    assert caught.value.code == "invalid_option_actor"


@pytest.mark.asyncio
async def test_registry_rejects_missing_or_drifted_database_metadata(
    db_session: AsyncSession,
) -> None:
    registry = PostgresOptionsRegistry()
    await registry.initialize(db_session)
    row = await db_session.get(DatabaseOption, "WOS_TORRENT_MAX_ACTIVE_PER_USER")
    assert row is not None
    row.maximum_value = 1_000
    await db_session.flush()

    with pytest.raises(DatabaseOptionsDriftError):
        await registry.snapshot(db_session)


@pytest.mark.asyncio
async def test_database_constraint_rejects_a_value_in_the_wrong_typed_column(
    db_session: AsyncSession,
) -> None:
    registry = PostgresOptionsRegistry()
    await registry.initialize(db_session)
    row = await db_session.get(DatabaseOption, "WOS_TORRENT_MAX_ACTIVE_PER_USER")
    assert row is not None
    row.integer_value = None
    row.boolean_value = True

    with pytest.raises(IntegrityError):
        await db_session.commit()
