"""Liveness file of the worker and scheduler containers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.runtime_heartbeat import runtime_heartbeat


@pytest.mark.asyncio
async def test_heartbeat_is_refreshed_while_the_service_runs_and_stops_after(
    tmp_path: Path,
) -> None:
    path = tmp_path / "heartbeat"

    async with runtime_heartbeat(path, interval=0.01):
        await asyncio.sleep(0.05)
        assert path.is_file()
        os.utime(path, (0, 0))
        await asyncio.sleep(0.05)
        assert path.stat().st_mtime > 0

    os.utime(path, (0, 0))
    await asyncio.sleep(0.05)
    assert path.stat().st_mtime == 0
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "runtime-heartbeat"]


@pytest.mark.asyncio
async def test_an_unwritable_heartbeat_never_stops_the_service(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    path = tmp_path / "heartbeat"
    path.symlink_to(target)

    async with runtime_heartbeat(path, interval=0.01):
        await asyncio.sleep(0.03)

    assert not target.exists()


@pytest.mark.asyncio
async def test_heartbeat_rejects_a_non_positive_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        async with runtime_heartbeat(tmp_path / "heartbeat", interval=0):
            pass
