import os
from pathlib import Path
from uuid import uuid4

from app.integrations.wos_restart import WosRestartStore


def test_wos_restart_uses_dedicated_fixed_control_directories(data_root: Path) -> None:
    control = data_root / ".wos-control"
    request_directory = control / "wos"
    status_directory = control / "wos-status"
    request_directory.mkdir(parents=True)
    status_directory.mkdir()
    control.chmod(0o700)
    request_directory.chmod(0o700)
    status_directory.chmod(0o750)
    store = WosRestartStore(data_root, status_owner_uid=os.geteuid())

    pending = store.request(uuid4())

    assert pending.state == "pending"
    assert (request_directory / "restart-request.json").is_file()
    assert not (control / "newgreedy" / "restart-request.json").exists()
