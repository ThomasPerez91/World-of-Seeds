"""Recovery grants: no account changes until a private, single-use token is redeemed."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.external.dependencies import lock_external_client_authorization
from app.api.external.errors import ExternalApiError
from app.auth.passwords import hash_password_async
from app.auth.security import generate_token, hash_token, tokens_match
from app.models import DiscordAccountLink, ExternalApiAudit, PasswordReset, User, UserSession

RESET_SCOPE = "users:reset-password"
RESET_TTL_SECONDS = 900
RESET_COOLDOWN_SECONDS = 60


class ResetUnavailable(Exception):
    pass


class ResetThrottled(Exception):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _eligible(user: User | None) -> bool:
    return user is not None and user.is_active and not user.is_admin and user.deleted_at is None


async def issue_password_reset(
    db: AsyncSession, *, client_id: UUID, key_hash: str, discord_id: str, request_id: str
) -> str:
    await db.rollback()
    await lock_external_client_authorization(
        db, client_id=client_id, key_hash=key_hash, scope=RESET_SCOPE
    )
    link = await db.get(DiscordAccountLink, discord_id)
    if link is None:
        raise ResetUnavailable
    user = await db.get(User, link.user_id, with_for_update=True, populate_existing=True)
    if not _eligible(user):
        raise ResetUnavailable
    assert user is not None
    grant = await db.get(PasswordReset, user.id, with_for_update=True, populate_existing=True)
    now = datetime.now(UTC)
    if grant is not None and now < _utc(grant.created_at) + timedelta(
        seconds=RESET_COOLDOWN_SECONDS
    ):
        raise ResetThrottled
    token = generate_token()
    if grant is None:
        grant = PasswordReset(user_id=user.id)
        db.add(grant)
    grant.client_id, grant.client_key_hash = client_id, key_hash
    grant.token_hash = hash_token(token)
    grant.password_fingerprint = hash_token(user.password_hash)
    grant.created_at = now
    grant.expires_at = now + timedelta(seconds=RESET_TTL_SECONDS)
    grant.consumed_at = None
    db.add(
        ExternalApiAudit(
            client_id=client_id,
            user_id=user.id,
            action="password_reset.issue",
            request_id=request_id,
        )
    )
    await db.commit()
    return token


async def confirm_password_reset(
    db: AsyncSession, *, token: str, password: str, request_id: str
) -> None:
    fingerprint = hash_token(token)
    candidate = await db.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == fingerprint)
    )
    if candidate is None:
        raise ResetUnavailable
    # Copy before ending the lookup transaction: no SQL connection during Argon2 work.
    user_id, client_id, key_hash = candidate.user_id, candidate.client_id, candidate.client_key_hash
    await db.rollback()
    encoded = await hash_password_async(password)
    try:
        await lock_external_client_authorization(
            db, client_id=client_id, key_hash=key_hash, scope=RESET_SCOPE
        )
    except ExternalApiError as exc:
        raise ResetUnavailable from exc
    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    grant = await db.get(PasswordReset, user_id, with_for_update=True, populate_existing=True)
    now = datetime.now(UTC)
    if (
        not _eligible(user)
        or grant is None
        or grant.consumed_at is not None
        or _utc(grant.expires_at) <= now
        or not tokens_match(grant.token_hash, fingerprint)
        or grant.client_id != client_id
        or grant.client_key_hash != key_hash
        or user is None
        or not tokens_match(grant.password_fingerprint, hash_token(user.password_hash))
    ):
        raise ResetUnavailable
    user.password_hash, user.updated_at = encoded, now
    # Initial accounts still personalize their username through the mandatory first-access screen.
    grant.consumed_at = now
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.add(
        ExternalApiAudit(
            client_id=client_id,
            user_id=user_id,
            action="password_reset.complete",
            request_id=request_id,
        )
    )
    await db.commit()
