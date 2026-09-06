#!/usr/bin/env python3
"""Secret-safe runtime probe used by the V2-33 Rise2 WebSocket gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select, text
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from app.auth.security import DUMMY_PASSWORD_HASH
from app.auth.service import issue_session
from app.coordination import RedisCoordinator
from app.core.config import get_settings
from app.core.database import session_factory
from app.models import User, UserSession


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("setup", "baseline", "reconnect", "redis-down", "resync-get", "cleanup"),
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--state-dir", default="/run/gate5")
    return parser.parse_args()


def load_secret(state_dir: Path) -> dict[str, Any]:
    value = json.loads((state_dir / "sessions.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or len(value.get("sessions", [])) != 25:
        raise RuntimeError("invalid gate5 session state")
    return value


async def setup(campaign: str, state_dir: Path) -> None:
    settings = get_settings()
    prefix = f"ws-{campaign}-"
    async with session_factory() as db:
        existing = await db.scalar(
            select(func.count(User.id)).where(User.username.like(f"{prefix}%"))
        )
        if existing:
            raise RuntimeError("gate5 campaign users already exist")
        rows: list[dict[str, str]] = []
        for index in range(25):
            user = User(
                username=f"{prefix}{index:02d}",
                password_hash=DUMMY_PASSWORD_HASH,
                is_active=True,
                must_change_credentials=False,
            )
            db.add(user)
            await db.flush()
            tokens = issue_session(db, user=user, settings=settings)
            rows.append({"user_id": str(user.id), "token": tokens.session_token})
        await db.commit()

    allowed_host = next(
        host
        for host in settings.allowed_hosts
        if host not in {"127.0.0.1", "localhost", "test"}
    )
    payload = {
        "session_cookie_name": settings.session_cookie_name,
        "allowed_host": allowed_host,
        "sessions": rows,
    }
    path = state_dir / "sessions.json"
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream)
        stream.write("\n")


async def cleanup(campaign: str) -> None:
    prefix = f"ws-{campaign}-"
    async with session_factory() as db:
        user_ids = tuple(
            (
                await db.scalars(select(User.id).where(User.username.like(f"{prefix}%")))
            ).all()
        )
        if user_ids:
            await db.execute(delete(UserSession).where(UserSession.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.commit()
        remaining = await db.scalar(
            select(func.count(User.id)).where(User.username.like(f"{prefix}%"))
        )
    if remaining:
        raise RuntimeError("gate5 cleanup left temporary users behind")


def open_socket(secret: dict[str, Any], token: str) -> ClientConnection:
    host = str(secret["allowed_host"])
    cookie_name = str(secret["session_cookie_name"])
    tcp_socket = socket.create_connection(("api", 8000), timeout=10)
    # The connection timeout is only for establishing TCP. Leaving it on the socket
    # would kill an otherwise healthy WebSocket before the 20-second application
    # heartbeat and turn the runtime gate into a false failure.
    tcp_socket.settimeout(None)
    return connect(
        f"ws://{host}:8000/api/v2/torrents/events",
        sock=tcp_socket,
        additional_headers={"Cookie": f"{cookie_name}={token}"},
        proxy=None,
        compression=None,
        ping_interval=None,
        open_timeout=10,
        close_timeout=2,
    )


def receive_type(socket_: ClientConnection, expected: str, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            payload = json.loads(socket_.recv(timeout=remaining))
        except (ConnectionClosed, TimeoutError, json.JSONDecodeError):
            return False
        if payload.get("type") == expected:
            return True
    return False


def wait_disconnected(socket_: ClientConnection, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket_.recv(timeout=deadline - time.monotonic())
        except ConnectionClosed:
            return True
        except TimeoutError:
            return False
    return False


async def publish_queue_event() -> bool:
    redis = RedisCoordinator.from_settings(get_settings())
    try:
        return await redis.publish_torrent_queue_changed(datetime.now(UTC))
    finally:
        await redis.aclose()


async def idle_transactions() -> int:
    async with session_factory() as db:
        value = await db.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND state = 'idle in transaction' "
                "AND pid <> pg_backend_pid()"
            )
        )
        await db.rollback()
    return int(value or 0)


def baseline(state_dir: Path) -> None:
    secret = load_secret(state_dir)
    tokens = [str(row["token"]) for row in secret["sessions"]]
    sockets: list[ClientConnection] = []
    try:
        tiers = (10, 25, 50, 100)
        for target in tiers:
            while len(sockets) < target:
                sockets.append(open_socket(secret, tokens[len(sockets) % len(tokens)]))

        # The server accepts the WebSocket before the Redis subscription is confirmed.
        # Waiting for one application heartbeat from every socket proves all 100
        # subscriptions reached the realtime loop before the fan-out event is injected.
        subscription_ready = sum(
            receive_type(socket_, "heartbeat", timeout=25) for socket_ in sockets
        )

        if not asyncio.run(publish_queue_event()):
            raise RuntimeError("baseline event publish failed")
        deliveries = sum(receive_type(socket_, "queue_changed") for socket_ in sockets)
        result = {
            "connections": len(sockets),
            "connection_tiers": list(tiers),
            "subscription_ready": subscription_ready,
            "event_deliveries": deliveries,
            "idle_transactions": asyncio.run(idle_transactions()),
        }
        (state_dir / "baseline.ready").write_text("ready\n", encoding="utf-8")
        result["api_restart_disconnects"] = sum(
            wait_disconnected(socket_) for socket_ in sockets
        )
        (state_dir / "baseline.json").write_text(
            json.dumps(result, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        for socket_ in sockets:
            try:
                socket_.close()
            except (ConnectionClosed, OSError):
                pass


def reconnect(state_dir: Path) -> dict[str, int]:
    secret = load_secret(state_dir)
    sockets = [open_socket(secret, str(row["token"])) for row in secret["sessions"]]
    try:
        subscription_ready = sum(
            receive_type(socket_, "heartbeat", timeout=25) for socket_ in sockets
        )
        if subscription_ready != len(sockets):
            raise RuntimeError(
                f"reconnect subscriptions not ready: {subscription_ready}/{len(sockets)}"
            )
        if not asyncio.run(publish_queue_event()):
            raise RuntimeError("reconnect event publish failed")
        deliveries = sum(receive_type(socket_, "queue_changed") for socket_ in sockets)
        return {"reconnections": len(sockets), "event_deliveries": deliveries}
    finally:
        for socket_ in sockets:
            socket_.close()


def redis_down(state_dir: Path) -> dict[str, int | bool]:
    secret = load_secret(state_dir)
    successes = 0
    for row in secret["sessions"]:
        socket_ = open_socket(secret, str(row["token"]))
        try:
            successes += receive_type(socket_, "resync_required")
        finally:
            try:
                socket_.close()
            except (ConnectionClosed, OSError):
                pass
    lost_event = not asyncio.run(publish_queue_event())
    return {
        "resync_attempts": 25,
        "resync_successes": successes,
        "lost_event_publish_observed": lost_event,
    }


def resync_get(state_dir: Path) -> dict[str, int]:
    secret = load_secret(state_dir)
    host = str(secret["allowed_host"])
    cookie_name = str(secret["session_cookie_name"])
    successes = 0
    with httpx.Client(base_url="http://api:8000", headers={"Host": host}, timeout=10) as client:
        for row in secret["sessions"]:
            response = client.get(
                "/api/v2/torrents",
                cookies={cookie_name: str(row["token"])},
            )
            successes += response.status_code == 200
    return {"authoritative_resync_successes": successes}


def main() -> int:
    args = parse_args()
    state_dir = Path(args.state_dir)
    if args.mode == "setup":
        asyncio.run(setup(args.campaign, state_dir))
    elif args.mode == "cleanup":
        asyncio.run(cleanup(args.campaign))
    elif args.mode == "baseline":
        baseline(state_dir)
    elif args.mode == "reconnect":
        print(json.dumps(reconnect(state_dir), sort_keys=True))
    elif args.mode == "redis-down":
        print(json.dumps(redis_down(state_dir), sort_keys=True))
    elif args.mode == "resync-get":
        print(json.dumps(resync_get(state_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
