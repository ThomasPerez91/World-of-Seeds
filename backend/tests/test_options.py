import os
from pathlib import Path

import pytest

from app.options import OptionsStore, OptionsUnsafeError, OptionsValidationError


def make_control(data_root: Path) -> Path:
    control = data_root / ".wos-control"
    control.mkdir()
    control.chmod(0o700)
    return control


def values(store: OptionsStore) -> dict[str, bool | int | str]:
    return {field.spec.key: field.value for field in store.read()}


def test_options_store_uses_safe_defaults_when_file_is_absent(data_root: Path) -> None:
    store = OptionsStore(data_root)

    loaded = values(store)

    assert loaded["WOS_TORRENT_MAX_ACTIVE_PER_USER"] == 5
    assert loaded["WOS_STORAGE_PRESSURE_WARNING_PERCENT"] == 80
    assert loaded["WOS_STORAGE_PRESSURE_CRITICAL_PERCENT"] == 90


def test_options_store_updates_atomically_and_keeps_last_valid_backup(data_root: Path) -> None:
    control = make_control(data_root)
    options = control / ".options"
    options.write_text("WOS_TORRENT_MAX_ACTIVE_PER_USER=6\n", encoding="utf-8")
    options.chmod(0o600)
    original_inode = options.stat().st_ino
    store = OptionsStore(data_root)

    first = store.update(
        {
            "WOS_TORRENT_MAX_ACTIVE_PER_USER": 7,
            "WOS_STORAGE_PRESSURE_WARNING_PERCENT": 75,
        }
    )

    assert first.changed_keys == (
        "WOS_TORRENT_MAX_ACTIVE_PER_USER",
        "WOS_STORAGE_PRESSURE_WARNING_PERCENT",
    )
    assert first.restart_required is False
    assert options.stat().st_ino != original_inode
    assert options.stat().st_mode & 0o777 == 0o600
    assert (control / ".options.backup").read_text(encoding="utf-8") == (
        "WOS_TORRENT_MAX_ACTIVE_PER_USER=6\n"
    )
    assert values(store)["WOS_TORRENT_MAX_ACTIVE_PER_USER"] == 7
    assert not list(control.glob("..options.*.tmp"))

    restarted = store.update({"WOS_WORKER_CONCURRENCY": 4})
    assert restarted.restart_required is True
    assert restarted.changed_keys == ("WOS_WORKER_CONCURRENCY",)
    assert "WOS_TORRENT_MAX_ACTIVE_PER_USER=7" in (control / ".options.backup").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"WOS_TORRENT_MAX_ACTIVE_PER_USER": 0}, "WOS_TORRENT_MAX_ACTIVE_PER_USER"),
        ({"WOS_STORAGE_PRESSURE_WARNING_PERCENT": 95}, "WOS_STORAGE_PRESSURE_WARNING_PERCENT"),
        ({"WOS_CACHE_PROGRESS_TTL_SECONDS": 301}, "WOS_CACHE_PROGRESS_TTL_SECONDS"),
        ({"WOS_DATABASE_PASSWORD": "leak"}, "WOS_DATABASE_PASSWORD"),
        ({"WOS_UNKNOWN": 1}, "WOS_UNKNOWN"),
    ],
)
def test_options_store_rejects_invalid_or_unknown_changes_without_writing(
    data_root: Path,
    changes: dict[str, int | str],
    field: str,
) -> None:
    control = make_control(data_root)
    store = OptionsStore(data_root)

    with pytest.raises(OptionsValidationError) as caught:
        store.update(changes)

    assert caught.value.field == field
    assert not (control / ".options").exists()


def test_options_store_rejects_unknown_file_key_and_unsafe_paths(data_root: Path) -> None:
    control = make_control(data_root)
    options = control / ".options"
    secret_value = "do-not-expose-this-value"
    options.write_text(f"WOS_QBITTORRENT_PASSWORD={secret_value}\n", encoding="utf-8")
    options.chmod(0o600)

    with pytest.raises(OptionsValidationError) as caught:
        OptionsStore(data_root).read()
    assert caught.value.code == "secret_option_forbidden"
    assert secret_value not in str(caught.value)

    options.unlink()
    outside = data_root.parent / "outside.options"
    outside.write_text("WOS_TORRENT_MAX_ACTIVE_PER_USER=5\n", encoding="utf-8")
    options.symlink_to(outside)
    with pytest.raises(OptionsUnsafeError):
        OptionsStore(data_root).read()

    options.unlink()
    options.write_text("WOS_TORRENT_MAX_ACTIVE_PER_USER=5\n", encoding="utf-8")
    os.chmod(options, 0o644)
    with pytest.raises(OptionsUnsafeError, match="permissions"):
        OptionsStore(data_root).read()
