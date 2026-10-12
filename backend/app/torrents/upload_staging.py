"""Keep upload preparation owned until its disk thread really finishes."""

import asyncio
import logging
import uuid
from functools import partial

from starlette.concurrency import run_in_threadpool

from app.jobs.torrent_payloads import (
    OwnedTorrentPayload,
    TorrentPayloadStore,
    TorrentPayloadStoreError,
)

logger = logging.getLogger(__name__)


async def _settle[T](task: asyncio.Task[T]) -> T:
    # Repeated cancellations cannot leave a still-writing thread behind or
    # let the request session close before its cleanup has completed.
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise


async def cleanup_upload_payload(store: TorrentPayloadStore, owned: OwnedTorrentPayload) -> None:
    task = asyncio.create_task(run_in_threadpool(store.remove_owned, owned))
    try:
        await _settle(task)
    except TorrentPayloadStoreError:
        logger.warning("torrent_payload_cleanup_failed")


async def stage_upload_payload(
    store: TorrentPayloadStore,
    content: bytes,
    *,
    storage_key: uuid.UUID,
    max_total_size: int,
) -> OwnedTorrentPayload:
    task = asyncio.create_task(
        run_in_threadpool(
            partial(
                store.stage_owned,
                content,
                storage_key=storage_key,
                max_total_size=max_total_size,
            )
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            owned = await _settle(task)
        except Exception:
            # Staging cleans its private attempt even when publication fails.
            logger.warning("torrent_payload_staging_failed_after_cancel")
        else:
            await cleanup_upload_payload(store, owned)
        raise
