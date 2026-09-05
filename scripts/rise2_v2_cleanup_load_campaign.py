#!/usr/bin/env python3
"""Delete one deterministic V2-33 scheduler-load campaign only."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

REGISTRY = Path("/run/secrets/integration_registry")
HELPER_PATH = Path(__file__).resolve().with_name("rise2_v2_scheduler_load.py")


def _load_helper():
    registry = REGISTRY.read_text(encoding="utf-8")
    if not registry or len(registry) > 1024 * 1024:
        raise RuntimeError("invalid integration registry")
    os.environ["WOS_INTEGRATION_ACCOUNTS_JSON"] = registry
    spec = importlib.util.spec_from_file_location("rise2_v2_cleanup_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load V2-33 scheduler helper")
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    return helper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    return parser.parse_args()


async def cleanup(campaign: str) -> dict[str, object]:
    helper = _load_helper()
    from sqlalchemy import delete

    from app.core.database import engine, session_factory
    from app.models import ManagedTorrent, User

    try:
        settings, routes = helper._safe_runtime()
        campaign = helper._campaign(campaign)
        fixtures = helper._fixtures(campaign, len(routes))

        async with session_factory() as session:
            before = await helper._campaign_counts(session, campaign)
            await session.rollback()

        await helper._remove_qbittorrent_fixtures(
            fixtures,
            routes,
            data_root=Path(settings.qbittorrent_data_root),
        )

        prefix = helper._prefix(campaign)
        async with session_factory() as session, session.begin():
            await session.execute(
                delete(ManagedTorrent).where(ManagedTorrent.name.like(f"{prefix}%"))
            )
            await session.execute(delete(User).where(User.username.like(f"{prefix}%")))

        async with session_factory() as session:
            after = await helper._campaign_counts(session, campaign)
            await session.rollback()

        if any(after.values()):
            raise RuntimeError("pilot campaign cleanup incomplete")

        return {
            "schema": "world-of-seeds-v2-rise2-load-cleanup/v1",
            "campaign": campaign,
            "before_users": before["users"],
            "before_torrents": before["torrents"],
            "before_requests": before["requests"],
            "qbittorrent_fixture_count": len(fixtures),
            "remaining_users": after["users"],
            "remaining_torrents": after["torrents"],
            "remaining_requests": after["requests"],
            "secrets_or_business_identifiers_in_report": False,
        }
    finally:
        await engine.dispose()


async def _main() -> int:
    args = parse_args()
    report = await cleanup(args.campaign)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
