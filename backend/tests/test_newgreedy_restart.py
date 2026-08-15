import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.integrations.newgreedy_restart import (
    NewGreedyRestartPendingError,
    NewGreedyRestartStore,
    NewGreedyRestartUnsafeError,
)


def make_control_tree(data_root: Path) -> tuple[NewGreedyRestartStore, Path, Path]:
    control_root = data_root / ".wos-control"
    request_directory = control_root / "newgreedy"
    status_directory = control_root / "newgreedy-status"
    request_directory.mkdir(parents=True)
    status_directory.mkdir()
    control_root.chmod(0o700)
    request_directory.chmod(0o700)
    status_directory.chmod(0o750)
    return NewGreedyRestartStore(data_root), request_directory, status_directory


def test_restart_store_creates_one_exclusive_request(data_root: Path) -> None:
    store, request_directory, _ = make_control_tree(data_root)
    requested_by = uuid4()

    assert store.status().state == "idle"
    pending = store.request(requested_by)

    assert pending.state == "pending"
    assert pending.request_id is not None
    request_file = request_directory / "restart-request.json"
    assert request_file.stat().st_mode & 0o777 == 0o600
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert UUID(payload["request_id"]) == pending.request_id
    assert UUID(payload["requested_by"]) == requested_by
    assert store.status() == pending
    assert not list(request_directory.glob(".restart-request.json.*.tmp"))

    with pytest.raises(NewGreedyRestartPendingError):
        store.request(requested_by)


def test_restart_store_reads_root_mediated_status(data_root: Path) -> None:
    store, request_directory, status_directory = make_control_tree(data_root)
    pending = store.request(uuid4())
    (request_directory / "restart-request.json").unlink()
    status_file = status_directory / "restart-status.json"
    status_file.write_text(
        json.dumps(
            {
                "state": "healthy",
                "request_id": str(pending.request_id),
                "updated_at": "2026-08-15T15:00:00+00:00",
                "message_code": "healthy",
            }
        ),
        encoding="utf-8",
    )
    status_file.chmod(0o640)

    current = store.status()

    assert current.state == "healthy"
    assert current.request_id == pending.request_id
    assert current.updated_at is not None
    assert current.updated_at.isoformat() == "2026-08-15T15:00:00+00:00"


def test_restart_store_refuses_forged_status_paths(data_root: Path) -> None:
    store, _, status_directory = make_control_tree(data_root)
    outside = data_root.parent / "outside-status.json"
    outside.write_text("{}", encoding="utf-8")
    (status_directory / "restart-status.json").symlink_to(outside)

    with pytest.raises(NewGreedyRestartUnsafeError):
        store.status()

    (status_directory / "restart-status.json").unlink()
    os.chmod(status_directory, 0o770)
    with pytest.raises(NewGreedyRestartUnsafeError, match="Status directory"):
        store.status()


def test_restart_store_does_not_queue_while_host_is_restarting(data_root: Path) -> None:
    store, _, status_directory = make_control_tree(data_root)
    status_file = status_directory / "restart-status.json"
    status_file.write_text(
        json.dumps(
            {
                "state": "restarting",
                "request_id": str(uuid4()),
                "updated_at": datetime.now(UTC).isoformat(),
                "message_code": "restarting",
            }
        ),
        encoding="utf-8",
    )
    status_file.chmod(0o640)

    with pytest.raises(NewGreedyRestartPendingError, match="already running"):
        store.request(uuid4())
