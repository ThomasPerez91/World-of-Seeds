"""Local diagnostic: python -m app.benchmark_password_work (not a Rise2 load test)."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import perf_counter

from app.auth.passwords import password_work_pool, verify_password_async
from app.auth.security import DUMMY_PASSWORD_HASH, verify_password


async def measure(operation: Callable[[], Awaitable[None]]) -> dict[str, float]:
    gaps: list[float] = []
    stopped = asyncio.Event()

    async def ticker() -> None:
        previous = perf_counter()
        while not stopped.is_set():
            await asyncio.sleep(0.01)
            current = perf_counter()
            gaps.append(current - previous)
            previous = current

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    start = perf_counter()
    try:
        await operation()
        elapsed = perf_counter() - start
        await asyncio.sleep(0.02)
    finally:
        stopped.set()
        await task
    return {"elapsed_seconds": round(elapsed, 4), "max_loop_gap_seconds": round(max(gaps), 4)}


async def main() -> None:
    async def blocking() -> None:
        for _ in range(4):
            verify_password("incorrect-password", DUMMY_PASSWORD_HASH)

    async def offloaded() -> None:
        await asyncio.gather(
            *(verify_password_async("incorrect-password", DUMMY_PASSWORD_HASH) for _ in range(4))
        )

    password_work_pool.start()
    try:
        print(
            json.dumps(
                {
                    "checks": 4,
                    "local_only": True,
                    "before": await measure(blocking),
                    "after": await measure(offloaded),
                },
                indent=2,
            )
        )
    finally:
        await password_work_pool.aclose()


if __name__ == "__main__":
    asyncio.run(main())
