from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    canonical_username,
    generate_auth_seed,
    generate_initial_password,
    generate_initial_username,
    hash_password,
    normalize_username,
)
from app.models import DatabaseOption, User, UserProvisioningAudit
from app.options import PostgresOptionsRegistry

MAX_GENERATION_ATTEMPTS = 20
QUOTA_OPTION_KEY = "WOS_MAX_USER_ACCOUNTS"
# Stable signed bigint used only as a PostgreSQL transaction-scoped mutex.
QUOTA_ADVISORY_LOCK_ID = 8_785_337_223


class UserAccountQuotaReachedError(Exception):
    pass


class UserProvisioningConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    user: User
    initial_password: str


class UserProvisioningService:
    """Single transactional entry point for every runtime account creation."""

    async def provision(
        self,
        session: AsyncSession,
        *,
        username: str | None = None,
        password: str | None = None,
        is_admin: bool = False,
        source: Literal["admin", "external_api", "cli"],
        actor_user_id: uuid.UUID | None = None,
        external_client_id: uuid.UUID | None = None,
    ) -> ProvisionedUser:
        await self.lock_creation(session)
        await self._check_quota(session)
        requested_username = normalize_username(username) if username is not None else None

        for _ in range(MAX_GENERATION_ATTEMPTS):
            candidate_username = requested_username or generate_initial_username()
            seed = generate_auth_seed()
            conflict = await session.scalar(
                select(User.id).where(
                    (func.lower(User.username) == canonical_username(candidate_username))
                    | (User.auth_seed == seed)
                )
            )
            if conflict is not None:
                if requested_username is not None:
                    raise UserProvisioningConflictError
                continue

            initial_password = password or generate_initial_password()
            user = User(
                username=candidate_username,
                password_hash=hash_password(initial_password),
                auth_seed=seed,
                is_admin=is_admin,
                must_change_credentials=password is None,
            )
            session.add(user)
            await session.flush()
            session.add(
                UserProvisioningAudit(
                    user_id=user.id,
                    source=source,
                    actor_user_id=actor_user_id,
                    external_client_id=external_client_id,
                )
            )
            await session.flush()
            return ProvisionedUser(user=user, initial_password=initial_password)

        raise UserProvisioningConflictError

    async def quota(self, session: AsyncSession) -> tuple[int, int]:
        await PostgresOptionsRegistry().initialize(session)
        row = await session.get(DatabaseOption, QUOTA_OPTION_KEY)
        if row is None or type(row.value) is not int:
            raise RuntimeError("account quota option is unavailable")
        count = int(
            await session.scalar(
                select(func.count()).select_from(User).where(User.deleted_at.is_(None))
            )
            or 0
        )
        return count, row.value

    async def lock_creation(self, session: AsyncSession) -> None:
        """Serialize account mutations before idempotency and quota decisions."""

        if session.get_bind().dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": QUOTA_ADVISORY_LOCK_ID},
            )
        await PostgresOptionsRegistry().initialize(session)

    async def _check_quota(self, session: AsyncSession) -> None:
        row = await session.scalar(
            select(DatabaseOption).where(DatabaseOption.key == QUOTA_OPTION_KEY).with_for_update()
        )
        if row is None or type(row.value) is not int:
            raise RuntimeError("account quota option is unavailable")
        count = int(
            await session.scalar(
                select(func.count()).select_from(User).where(User.deleted_at.is_(None))
            )
            or 0
        )
        if count >= row.value:
            raise UserAccountQuotaReachedError
