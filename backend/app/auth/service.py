from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    DUMMY_PASSWORD_HASH,
    canonical_username,
    generate_initial_password,
    generate_initial_username,
    generate_token,
    hash_password,
    hash_token,
    normalize_username,
    throttle_key,
    verify_password,
)
from app.core.config import Settings
from app.files import WorkspaceAlreadyExistsError, WorkspaceError, WorkspaceManager
from app.models import LoginThrottle, User, UserSession


class AuthenticationFailedError(Exception):
    pass


class AuthenticationLockedError(Exception):
    pass


class UsernameUnavailableError(Exception):
    pass


class ManagedUserNotFoundError(Exception):
    pass


class ProtectedUserError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    expires_at: datetime


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def user_can_login(user: User, now: datetime) -> bool:
    del now
    return user.is_active and user.deleted_at is None


async def _register_failure(
    db: AsyncSession,
    key: str,
    throttle: LoginThrottle | None,
    settings: Settings,
    now: datetime,
) -> None:
    window = timedelta(minutes=settings.auth_attempt_window_minutes)
    if throttle is None:
        throttle = LoginThrottle(key_hash=key, failures=1, window_started_at=now)
        db.add(throttle)
    elif now - ensure_utc(throttle.window_started_at) >= window:
        throttle.failures = 1
        throttle.window_started_at = now
        throttle.locked_until = None
    else:
        throttle.failures += 1

    if throttle.failures >= settings.auth_max_attempts:
        throttle.locked_until = now + timedelta(minutes=settings.auth_lock_minutes)

    try:
        await db.commit()
    except IntegrityError:
        # A concurrent first failure can race on the throttle primary key.
        await db.rollback()
        concurrent = await db.scalar(
            select(LoginThrottle).where(LoginThrottle.key_hash == key).with_for_update()
        )
        if concurrent is None:
            raise
        concurrent.failures += 1
        if concurrent.failures >= settings.auth_max_attempts:
            concurrent.locked_until = now + timedelta(minutes=settings.auth_lock_minutes)
        await db.commit()


async def authenticate(
    db: AsyncSession,
    *,
    username_input: str,
    password: str,
    client_ip: str,
    settings: Settings,
) -> tuple[User, SessionTokens]:
    now = datetime.now(UTC)
    try:
        username = normalize_username(username_input)
        username_key = canonical_username(username)
    except ValueError:
        username = username_input.strip()[:32]
        username_key = username.lower()

    key = throttle_key(client_ip, username)
    throttle = await db.scalar(
        select(LoginThrottle).where(LoginThrottle.key_hash == key).with_for_update()
    )
    if (
        throttle is not None
        and throttle.locked_until is not None
        and ensure_utc(throttle.locked_until) > now
    ):
        verify_password(password, DUMMY_PASSWORD_HASH)
        raise AuthenticationLockedError

    user = await db.scalar(select(User).where(func.lower(User.username) == username_key))
    encoded_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = verify_password(password, encoded_hash)

    if user is None or not password_matches or not user_can_login(user, now):
        await _register_failure(db, key, throttle, settings, now)
        raise AuthenticationFailedError

    if throttle is not None:
        await db.delete(throttle)

    tokens = issue_session(db, user=user, settings=settings, now=now)
    await db.commit()
    return user, tokens


def issue_session(
    db: AsyncSession,
    *,
    user: User,
    settings: Settings,
    now: datetime | None = None,
) -> SessionTokens:
    issued_at = now or datetime.now(UTC)
    session_token = generate_token()
    csrf_token = generate_token()
    expires_at = issued_at + timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            expires_at=expires_at,
        )
    )
    return SessionTokens(
        session_token=session_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


async def change_credentials(
    db: AsyncSession,
    *,
    user: User,
    current_password: str,
    username_input: str,
    new_password: str,
    settings: Settings,
    workspace_manager: WorkspaceManager,
) -> SessionTokens:
    locked_user = await db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_user is None or not verify_password(current_password, locked_user.password_hash):
        raise AuthenticationFailedError

    username = normalize_username(username_input)
    existing_user = await db.scalar(
        select(User).where(
            func.lower(User.username) == canonical_username(username),
            User.id != locked_user.id,
        )
    )
    if existing_user is not None:
        raise UsernameUnavailableError

    now = datetime.now(UTC)
    old_username = locked_user.username
    try:
        with workspace_manager.rename_for_transaction(old_username, username):
            locked_user.username = username
            locked_user.password_hash = hash_password(new_password)
            locked_user.must_change_credentials = False
            locked_user.updated_at = now

            await db.execute(
                update(UserSession)
                .where(UserSession.user_id == locked_user.id, UserSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            tokens = issue_session(db, user=locked_user, settings=settings, now=now)
            try:
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    except IntegrityError as exc:
        raise UsernameUnavailableError from exc
    return tokens


async def revoke_session(db: AsyncSession, user_session: UserSession) -> None:
    user_session.revoked_at = datetime.now(UTC)
    await db.commit()


async def create_managed_user(
    db: AsyncSession,
    *,
    workspace_manager: WorkspaceManager,
) -> tuple[User, str]:
    for _ in range(10):
        username = generate_initial_username()
        exists = await db.scalar(
            select(User.id).where(func.lower(User.username) == canonical_username(username))
        )
        if exists is not None:
            continue

        initial_password = generate_initial_password()
        user = User(
            username=username,
            password_hash=hash_password(initial_password),
            must_change_credentials=True,
        )
        try:
            with workspace_manager.provision_for_transaction(username):
                db.add(user)
                try:
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
        except (IntegrityError, WorkspaceAlreadyExistsError):
            continue

        await db.refresh(user)
        return user, initial_password

    raise WorkspaceError("Unable to generate a unique user workspace")


async def set_managed_user_active(
    db: AsyncSession,
    *,
    user_id: UUID,
    is_active: bool,
) -> User:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.deleted_at is not None:
        raise ManagedUserNotFoundError
    if user.is_admin:
        raise ProtectedUserError

    now = datetime.now(UTC)
    user.is_active = is_active
    user.updated_at = now
    if not is_active:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    await db.commit()
    await db.refresh(user)
    return user


async def delete_managed_user(db: AsyncSession, *, user_id: UUID) -> None:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.deleted_at is not None:
        raise ManagedUserNotFoundError
    if user.is_admin:
        raise ProtectedUserError

    now = datetime.now(UTC)
    user.is_active = False
    user.deleted_at = now
    user.updated_at = now
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()


async def purge_expired_sessions(db: AsyncSession) -> int:
    deleted_ids = await db.scalars(
        delete(UserSession)
        .where(UserSession.expires_at <= datetime.now(UTC))
        .returning(UserSession.id)
    )
    await db.commit()
    return len(deleted_ids.all())
