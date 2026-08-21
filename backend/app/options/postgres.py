from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatabaseOption, DatabaseOptionAudit, User
from app.options.registry import (
    OPTION_SPECS,
    OPTION_SPECS_BY_KEY,
    OptionSpec,
    OptionValue,
    is_sensitive_option_key,
)
from app.options.store import (
    OptionsValidationError,
    normalize_option_value,
    validate_cross_options,
)


class DatabaseOptionsDriftError(RuntimeError):
    """Raised when SQL option metadata no longer matches the code registry."""


@dataclass(frozen=True, slots=True)
class DatabaseOptionsUpdate:
    values: dict[str, OptionValue]
    changed_keys: tuple[str, ...]
    versions: dict[str, int]
    restart_required: bool


class PostgresOptionsRegistry:
    """PostgreSQL-authoritative V2 options with typed values and immutable audit events."""

    async def initialize(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        changed_at = now or datetime.now(UTC)
        rows = await self._load_rows(session, lock=True)
        self._reject_unknown_rows(rows)

        for spec in OPTION_SPECS:
            row = rows.get(spec.key)
            if row is None:
                row = self._new_row(spec, changed_at)
                session.add(row)
                session.add(
                    DatabaseOptionAudit(
                        option=row,
                        version=1,
                        old_value=None,
                        new_value=spec.default,
                        actor_user_id=None,
                        change_source="bootstrap",
                        changed_at=changed_at,
                    )
                )
                rows[spec.key] = row
            else:
                self._validate_row_metadata(row, spec)

        self._validated_values(rows)
        await session.flush()

    async def snapshot(self, session: AsyncSession) -> dict[str, OptionValue]:
        rows = await self._load_rows(session)
        return self._validated_values(rows)

    async def update(
        self,
        session: AsyncSession,
        changes: Mapping[str, OptionValue],
        *,
        actor_user_id: uuid.UUID,
        now: datetime | None = None,
    ) -> DatabaseOptionsUpdate:
        if not changes:
            raise OptionsValidationError("Au moins une option doit être modifiée.")
        actor = await session.get(User, actor_user_id)
        if actor is None or not actor.is_admin or not actor.is_active:
            raise OptionsValidationError(
                "L’administrateur responsable de la modification est invalide.",
                code="invalid_option_actor",
            )

        rows = await self._load_rows(session, lock=True)
        values = self._validated_values(rows)
        normalized_changes: dict[str, OptionValue] = {}
        for key, candidate in changes.items():
            spec = OPTION_SPECS_BY_KEY.get(key)
            if spec is None:
                code = (
                    "secret_option_forbidden" if is_sensitive_option_key(key) else "unknown_option"
                )
                raise OptionsValidationError(
                    "Cette option n’est pas administrable.",
                    code=code,
                    field=key,
                )
            if not spec.editable:
                raise OptionsValidationError(
                    "Cette option est en lecture seule.",
                    code="readonly_option",
                    field=key,
                )
            normalized = normalize_option_value(spec, candidate)
            normalized_changes[key] = normalized
            values[key] = normalized

        validate_cross_options(values)
        changed_at = now or datetime.now(UTC)
        changed_keys: list[str] = []
        versions: dict[str, int] = {}
        for key, normalized in normalized_changes.items():
            row = rows[key]
            if row.value == normalized:
                continue
            old_value = row.value
            spec = OPTION_SPECS_BY_KEY[key]
            row.set_value(spec, normalized)
            row.version += 1
            row.updated_by_user_id = actor_user_id
            row.updated_at = changed_at
            session.add(
                DatabaseOptionAudit(
                    option_key=key,
                    version=row.version,
                    old_value=old_value,
                    new_value=normalized,
                    actor_user_id=actor_user_id,
                    change_source="admin",
                    changed_at=changed_at,
                )
            )
            changed_keys.append(key)
            versions[key] = row.version

        await session.flush()
        return DatabaseOptionsUpdate(
            values=values,
            changed_keys=tuple(changed_keys),
            versions=versions,
            restart_required=any(OPTION_SPECS_BY_KEY[key].restart_required for key in changed_keys),
        )

    @staticmethod
    async def _load_rows(
        session: AsyncSession,
        *,
        lock: bool = False,
    ) -> dict[str, DatabaseOption]:
        statement = select(DatabaseOption).order_by(DatabaseOption.key)
        if lock:
            statement = statement.with_for_update()
        return {row.key: row for row in (await session.scalars(statement)).all()}

    def _validated_values(
        self,
        rows: Mapping[str, DatabaseOption],
    ) -> dict[str, OptionValue]:
        self._reject_unknown_rows(rows)
        missing = set(OPTION_SPECS_BY_KEY).difference(rows)
        if missing:
            raise DatabaseOptionsDriftError(
                f"Database option registry is missing {len(missing)} required entries"
            )
        values: dict[str, OptionValue] = {}
        for spec in OPTION_SPECS:
            row = rows[spec.key]
            self._validate_row_metadata(row, spec)
            values[spec.key] = normalize_option_value(spec, row.value)
        validate_cross_options(values)
        return values

    @staticmethod
    def _reject_unknown_rows(rows: Mapping[str, DatabaseOption]) -> None:
        unknown = set(rows).difference(OPTION_SPECS_BY_KEY)
        if unknown:
            raise DatabaseOptionsDriftError(
                f"Database option registry contains {len(unknown)} unknown entries"
            )

    @staticmethod
    def _validate_row_metadata(row: DatabaseOption, spec: OptionSpec) -> None:
        expected_minimum = spec.minimum if spec.input_type == "integer" else None
        expected_maximum = spec.maximum if spec.input_type == "integer" else None
        if (
            row.value_type != spec.input_type
            or row.minimum_value != expected_minimum
            or row.maximum_value != expected_maximum
            or row.choices != list(spec.choices)
            or row.editable != spec.editable
            or row.restart_required != spec.restart_required
        ):
            raise DatabaseOptionsDriftError(f"Database option metadata drifted for {spec.key}")

    @staticmethod
    def _new_row(spec: OptionSpec, now: datetime) -> DatabaseOption:
        row = DatabaseOption(
            key=spec.key,
            value_type=spec.input_type,
            minimum_value=spec.minimum if spec.input_type == "integer" else None,
            maximum_value=spec.maximum if spec.input_type == "integer" else None,
            choices=list(spec.choices),
            editable=spec.editable,
            restart_required=spec.restart_required,
            version=1,
            created_at=now,
            updated_at=now,
        )
        row.set_value(spec, spec.default)
        return row
