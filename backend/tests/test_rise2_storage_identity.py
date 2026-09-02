from pathlib import Path


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_rise2_env_uses_one_storage_uid_for_wos_and_qbittorrent() -> None:
    env = (_repository() / "deploy" / ".env.rise2.v2.example").read_text(encoding="utf-8")

    assert "WOS_V2_APP_UID=10001" in env
    assert "WOS_V2_QBITTORRENT_UID=10001" in env
    assert "WOS_V2_QBITTORRENT_GID=10002" in env


def test_rise2_preflight_rejects_an_incompatible_storage_identity() -> None:
    script = (_repository() / "scripts" / "rise2_v2_preflight.sh").read_text(encoding="utf-8")

    assert '[ "$qbittorrent_uid" = "$app_uid" ]' in script
    assert "storage root owner must match the shared WOS/qBittorrent UID" in script
    assert "storage root group must match the WOS application GID" in script
    assert "storage root mode must be 0750" in script
    assert 'sh "$repository/scripts/rise2_v2_storage_smoke.sh" "$environment"' in script


def test_rise2_storage_smoke_exercises_both_immutable_runtime_identities() -> None:
    script = (_repository() / "scripts" / "rise2_v2_storage_smoke.sh").read_text(
        encoding="utf-8"
    )

    assert "WorkspaceManager(Path(\"/data\"))" in script
    assert "manager.create(username)" in script
    assert "mode != 0o750" in script
    assert '--user "$qbittorrent_uid:$qbittorrent_gid"' in script
    assert 'printf "qB storage probe\\n" >"$file"' in script
    assert "source.rename(destination)" in script
    assert "destination.unlink()" in script
    assert "manager.remove_empty(username)" in script
    assert "docker compose" in script
    assert "--no-deps" in script
