"""Portable WoS account backup. Run from an operator shell, never from an API route."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.models import Base


class BackupError(Exception):
    pass


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    username: str
    password_hash: str
    auth_seed: str
    is_admin: bool
    is_active: bool
    must_change_credentials: bool
    preferred_locale: str
    deleted_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    discord_user_id: str
    user_id: UUID
    client_id: UUID
    created_at: datetime


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int
    accounts: list[Account]
    discord_links: list[Link]


async def export_accounts(conn: AsyncConnection) -> Snapshot:
    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    users = Base.metadata.tables["users"]
    links = Base.metadata.tables["discord_account_links"]
    account_rows = await conn.execute(
        select(*(users.c[name] for name in Account.model_fields)).order_by(users.c.id)
    )
    link_rows = await conn.execute(
        select(*(links.c[name] for name in Link.model_fields)).order_by(links.c.discord_user_id)
    )
    return Snapshot(
        version=1,
        accounts=[Account.model_validate(dict(row)) for row in account_rows.mappings()],
        discord_links=[Link.model_validate(dict(row)) for row in link_rows.mappings()],
    )


async def restore_accounts(
    conn: AsyncConnection, snapshot: Snapshot, client_map: dict[UUID, UUID]
) -> None:
    if snapshot.version != 1:
        raise BackupError("Unsupported account backup version")
    users = Base.metadata.tables["users"]
    links = Base.metadata.tables["discord_account_links"]
    clients = Base.metadata.tables["external_api_clients"]
    if set(client_map) != {link.client_id for link in snapshot.discord_links}:
        raise BackupError("Provide exactly one destination client for each source Discord client")
    await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    await conn.execute(text("LOCK TABLE users, discord_account_links IN SHARE ROW EXCLUSIVE MODE"))
    if (await conn.execute(select(users.c.id).limit(1))).first() is not None:
        raise BackupError("Restore requires an empty users table")
    if client_map:
        active_clients = await conn.execute(
            select(clients.c.id)
            .where(clients.c.id.in_(client_map.values()), clients.c.is_active.is_(True))
            .with_for_update()
        )
        if set(active_clients.scalars()) != set(client_map.values()):
            raise BackupError("All destination Discord clients must exist and be active")
    if snapshot.accounts:
        await conn.execute(users.insert(), [row.model_dump() for row in snapshot.accounts])
    if snapshot.discord_links:
        await conn.execute(
            links.insert(),
            [
                {**row.model_dump(), "client_id": client_map[row.client_id]}
                for row in snapshot.discord_links
            ],
        )


def client_mapping(values: list[str]) -> dict[UUID, UUID]:
    result: dict[UUID, UUID] = {}
    for value in values:
        source, sep, target = value.partition("=")
        if not sep:
            raise BackupError("Use --client SOURCE_UUID=DESTINATION_UUID")
        source_id = UUID(source)
        if source_id in result:
            raise BackupError("Duplicate source Discord client")
        result[source_id] = UUID(target)
    return result


async def run(args: argparse.Namespace) -> None:
    if args.operation != "export":
        decrypted = await asyncio.to_thread(
            subprocess.run,
            ["age", "--decrypt", "-i", str(args.identity), "--", str(args.archive)],
            stdout=subprocess.PIPE,
            check=True,
        )
        snapshot = Snapshot.model_validate_json(decrypted.stdout)
        if snapshot.version != 1:
            raise BackupError("Unsupported account backup version")
        if args.operation == "inspect":
            print(
                f"Accounts: {len(snapshot.accounts)}; Discord links: {len(snapshot.discord_links)}"
            )
            for source in sorted({link.client_id for link in snapshot.discord_links}):
                print(f"Source Discord client: {source}")
            return

    url = os.environ.get("WOS_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise BackupError("Set WOS_DATABASE_URL explicitly to a PostgreSQL asyncpg URL")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        if args.operation == "export":
            if sys.stdout.isatty():
                raise BackupError("Redirect the encrypted archive to a file")
            async with engine.begin() as conn:
                snapshot = await export_accounts(conn)
            await asyncio.to_thread(
                subprocess.run,
                ["age", "--encrypt", "--recipient", args.recipient],
                input=snapshot.model_dump_json().encode(),
                check=True,
            )
        else:
            async with engine.begin() as conn:
                await restore_accounts(conn, snapshot, client_mapping(args.client))
            print(
                f"Restored {len(snapshot.accounts)} accounts and "
                f"{len(snapshot.discord_links)} links"
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("export").add_argument("--recipient", required=True)
    for operation in ("inspect", "restore"):
        command = sub.add_parser(operation)
        command.add_argument("--identity", type=Path, required=True)
        command.add_argument("archive", type=Path)
        if operation == "restore":
            command.add_argument("--client", action="append", default=[])
            command.add_argument("--empty-target", action="store_true", required=True)
    try:
        asyncio.run(run(parser.parse_args()))
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValidationError, SQLAlchemyError, ValueError, OSError, subprocess.SubprocessError):
        print(
            "Account backup operation failed; check archive, database and configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
