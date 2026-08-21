from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.integrations.qbittorrent_v2 import (
    QBittorrentV2DesiredControl,
    QBittorrentV2RunState,
)
from app.options import OptionValue
from app.scheduler.weighted_fair import SchedulerResult


@dataclass(frozen=True, slots=True)
class ManagedTorrentControlIdentity:
    torrent_id: uuid.UUID
    info_hash: str
    storage_key: uuid.UUID
    qbittorrent_account_ref: uuid.UUID | None = None


def build_qbittorrent_control_plan(
    result: SchedulerResult,
    torrents: Sequence[ManagedTorrentControlIdentity],
    *,
    options: Mapping[str, OptionValue],
) -> tuple[QBittorrentV2DesiredControl, ...]:
    """Translate one scheduler result into an ordered, complete qB control plan.

    ``torrents`` is the caller's bounded control set, not the whole qB instance. Selected
    torrents are emitted in scheduler priority order; the rest are stopped and uncapped.
    """
    identities = tuple(torrents)
    torrent_ids = [identity.torrent_id for identity in identities]
    info_hashes = [identity.info_hash for identity in identities]
    storage_keys = [identity.storage_key for identity in identities]
    if len(torrent_ids) != len(set(torrent_ids)):
        raise ValueError("Each physical torrent must have one control identity")
    if len(info_hashes) != len(set(info_hashes)):
        raise ValueError("Each qB infohash must have one control identity")
    if len(storage_keys) != len(set(storage_keys)):
        raise ValueError("Each storage key must have one control identity")

    limit = options.get("WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL")
    if type(limit) is not int or not 0 <= limit <= 10_000_000_000:
        raise ValueError("WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL is invalid")

    by_torrent_id = {identity.torrent_id: identity for identity in identities}
    selected_ids = tuple(decision.candidate.torrent_id for decision in result.selected)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Scheduler result selects a physical torrent more than once")
    missing = set(selected_ids).difference(by_torrent_id)
    if missing:
        raise ValueError("Scheduler result has no qB control identity")

    shares = _download_limit_shares(limit, len(selected_ids))
    plan = [
        QBittorrentV2DesiredControl(
            info_hash=by_torrent_id[torrent_id].info_hash,
            storage_key=by_torrent_id[torrent_id].storage_key,
            run_state=QBittorrentV2RunState.RUNNING,
            download_limit_bytes_per_second=shares[index],
            qbittorrent_account_ref=by_torrent_id[torrent_id].qbittorrent_account_ref,
        )
        for index, torrent_id in enumerate(selected_ids)
    ]
    selected_set = set(selected_ids)
    for identity in sorted(identities, key=lambda item: str(item.torrent_id)):
        if identity.torrent_id not in selected_set:
            plan.append(
                QBittorrentV2DesiredControl(
                    info_hash=identity.info_hash,
                    storage_key=identity.storage_key,
                    run_state=QBittorrentV2RunState.STOPPED,
                    download_limit_bytes_per_second=0,
                    qbittorrent_account_ref=identity.qbittorrent_account_ref,
                )
            )
    return tuple(plan)


def _download_limit_shares(global_limit: int, active_count: int) -> tuple[int, ...]:
    if active_count == 0:
        return ()
    if global_limit == 0:
        return (0,) * active_count
    if global_limit < active_count:
        raise ValueError("Global qB download limit is too small for active torrents")
    base, remainder = divmod(global_limit, active_count)
    return tuple(base + (1 if index < remainder else 0) for index in range(active_count))
