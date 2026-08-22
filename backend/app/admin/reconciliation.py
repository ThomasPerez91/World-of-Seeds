from dataclasses import dataclass
from typing import Literal

from app.integrations.qbittorrent_v2 import QBittorrentV2Inventory
from app.models import ManagedTorrent, ManagedTorrentState
from app.storage.shared import SharedContentInventory

type ReconciliationSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True, slots=True)
class ReconciliationAnomaly:
    code: str
    severity: ReconciliationSeverity
    resource_id: str | None
    action: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    database_scanned: int
    qbittorrent_scanned: int
    storage_scanned: int
    external_torrents: int
    anomalies: tuple[ReconciliationAnomaly, ...]
    truncated: bool


def reconcile_inventory(
    torrents: tuple[ManagedTorrent, ...],
    *,
    database_truncated: bool,
    qbittorrent: QBittorrentV2Inventory | None,
    storage: SharedContentInventory | None,
) -> ReconciliationReport:
    """Compare bounded read-only inventories without mutating qB, SQL, or storage."""
    anomalies: list[ReconciliationAnomaly] = []
    by_hash = {torrent.info_hash: torrent for torrent in torrents}
    by_storage = {torrent.storage_key: torrent for torrent in torrents}
    qb_items = qbittorrent.items if qbittorrent is not None else ()
    qb_by_hash = {item.info_hash: item for item in qb_items}
    storage_keys = set(storage.keys if storage is not None else ())

    if qbittorrent is None:
        anomalies.append(
            ReconciliationAnomaly("qbittorrent_unavailable", "warning", None, "retry_inventory")
        )
    if storage is None:
        anomalies.append(
            ReconciliationAnomaly("storage_unavailable", "critical", None, "inspect_storage")
        )

    for torrent in torrents:
        resource_id = str(torrent.id)
        expects_physical = torrent.state not in {
            ManagedTorrentState.PENDING,
            ManagedTorrentState.PURGED,
        }
        if (
            expects_physical
            and qbittorrent is not None
            and not qbittorrent.truncated
            and torrent.info_hash not in qb_by_hash
        ):
            anomalies.append(
                ReconciliationAnomaly("missing_qb_torrent", "critical", resource_id, "retry_job")
            )
        qb_item = qb_by_hash.get(torrent.info_hash)
        if qb_item is not None and qb_item.storage_key != torrent.storage_key:
            anomalies.append(
                ReconciliationAnomaly(
                    "qb_identity_mismatch", "critical", resource_id, "manual_review"
                )
            )
        if expects_physical and storage is not None and torrent.storage_key not in storage_keys:
            anomalies.append(
                ReconciliationAnomaly("missing_storage", "critical", resource_id, "manual_review")
            )

    external = 0
    for item in qb_items:
        if not item.claims_wos_identity:
            external += 1
            continue
        matched_torrent = by_hash.get(item.info_hash)
        if matched_torrent is None and not database_truncated:
            anomalies.append(
                ReconciliationAnomaly("orphan_wos_qb", "warning", None, "manual_review")
            )
    if external:
        anomalies.append(ReconciliationAnomaly("external_torrents_read_only", "info", None, "none"))

    if storage is not None:
        if storage.invalid_entries:
            anomalies.append(
                ReconciliationAnomaly("unsafe_storage_entries", "critical", None, "manual_review")
            )
        if not database_truncated:
            for key in storage_keys.difference(by_storage):
                anomalies.append(
                    ReconciliationAnomaly("orphan_storage", "warning", str(key), "manual_review")
                )

    return ReconciliationReport(
        database_scanned=len(torrents),
        qbittorrent_scanned=len(qb_items),
        storage_scanned=len(storage_keys),
        external_torrents=external,
        anomalies=tuple(anomalies),
        truncated=(
            database_truncated
            or (qbittorrent.truncated if qbittorrent is not None else False)
            or (storage.truncated if storage is not None else False)
        ),
    )
