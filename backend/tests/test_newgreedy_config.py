import os
from pathlib import Path

import pytest

from app.integrations.newgreedy_config import (
    NewGreedyConfigStore,
    NewGreedyConfigUnsafeError,
    NewGreedyConfigValidationError,
)

SAMPLE_CONFIG = """; configuration NewGreedy
[proxy]
listen_port = 3456
tracker_timeout = 5

[spoofing]
upload_mode = ratio_based
target_ratio = 1.60 ; garder ce commentaire
auto_stop_at_target = true

[anti_detection]
port_range = 6881-6999
tracker_whitelist =

[web]
web_enabled = true
web_host = 0.0.0.0
web_port = 8080

[advanced]
log_level = INFO
inject_hours = 0-23
"""


def make_store(data_root: Path) -> tuple[NewGreedyConfigStore, Path]:
    control = data_root / ".wos-control" / "newgreedy"
    control.mkdir(parents=True)
    control.chmod(0o700)
    config = control / "config.ini"
    config.write_text(SAMPLE_CONFIG, encoding="utf-8")
    config.chmod(0o600)
    return NewGreedyConfigStore(data_root), config


def test_config_store_reads_typed_fields_and_updates_atomically(data_root: Path) -> None:
    store, config = make_store(data_root)
    original_inode = config.stat().st_ino

    fields = {field.spec.identifier: field.value for field in store.read()}
    assert fields["proxy.listen_port"] == 3456
    assert fields["spoofing.target_ratio"] == 1.6
    assert fields["spoofing.auto_stop_at_target"] is True

    updated = {
        field.spec.identifier: field.value
        for field in store.update(
            {
                "spoofing.target_ratio": 2.25,
                "spoofing.auto_stop_at_target": False,
                "advanced.log_level": "WARNING",
            }
        )
    }

    assert updated["spoofing.target_ratio"] == 2.25
    assert updated["spoofing.auto_stop_at_target"] is False
    assert updated["advanced.log_level"] == "WARNING"
    assert config.stat().st_ino != original_inode
    assert config.stat().st_mode & 0o777 == 0o600
    content = config.read_text(encoding="utf-8")
    assert "target_ratio = 2.25 ; garder ce commentaire" in content
    assert "; configuration NewGreedy" in content
    assert not list(config.parent.glob(".config.ini.*.tmp"))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"proxy.listen_port": 8081}, "read-only"),
        ({"spoofing.target_ratio": True}, "numeric"),
        ({"anti_detection.port_range": "0-70000"}, "invalid"),
        ({"advanced.log_level": "TRACE"}, "supported"),
        ({"unknown.value": "x"}, "Unknown"),
    ],
)
def test_config_store_rejects_unsupported_changes(
    data_root: Path,
    changes: dict[str, bool | int | str],
    expected: str,
) -> None:
    store, config = make_store(data_root)
    original = config.read_bytes()

    with pytest.raises(NewGreedyConfigValidationError, match=expected):
        store.update(changes)

    assert config.read_bytes() == original


def test_config_store_refuses_symlink_and_unsafe_permissions(data_root: Path) -> None:
    store, config = make_store(data_root)
    outside = data_root.parent / "outside.ini"
    outside.write_text(SAMPLE_CONFIG, encoding="utf-8")
    config.unlink()
    config.symlink_to(outside)

    with pytest.raises(NewGreedyConfigUnsafeError):
        store.read()

    config.unlink()
    config.write_text(SAMPLE_CONFIG, encoding="utf-8")
    os.chmod(config, 0o666)
    with pytest.raises(NewGreedyConfigUnsafeError, match="permissions"):
        store.update({"spoofing.target_ratio": 2.0})


def test_config_store_refuses_symlinked_control_directory(data_root: Path) -> None:
    outside = data_root.parent / "outside-control"
    outside.mkdir()
    (data_root / ".wos-control").symlink_to(outside, target_is_directory=True)

    with pytest.raises(NewGreedyConfigUnsafeError):
        NewGreedyConfigStore(data_root).read()
