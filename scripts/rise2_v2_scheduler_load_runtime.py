#!/usr/bin/env python3
"""Run the V2-33 scheduler load harness with the production Redis coordinator."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.coordination import RedisCoordinator
from app.core.config import get_settings


def _load_helper() -> ModuleType:
    path = Path(__file__).with_name("rise2_v2_scheduler_load.py")
    spec = importlib.util.spec_from_file_location("rise2_v2_scheduler_load", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("scheduler load helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = _load_helper()
_base_runtime = helper.SchedulerRuntime
_redis_clients: list[RedisCoordinator] = []


class _ProductionRedisSchedulerRuntime(_base_runtime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if "redis" in kwargs:
            raise RuntimeError("scheduler load runtime Redis override is forbidden")
        redis = RedisCoordinator.from_settings(get_settings())
        _redis_clients.append(redis)
        kwargs["redis"] = redis
        super().__init__(*args, **kwargs)


helper.SchedulerRuntime = _ProductionRedisSchedulerRuntime


async def _main() -> int:
    try:
        return await helper._main()
    finally:
        for redis in reversed(_redis_clients):
            await redis.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
