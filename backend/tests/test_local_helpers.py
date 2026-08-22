import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from app import local_smoke_seed, local_tracker_fixture
from app.torrents import sanitize_torrent


@pytest.mark.asyncio
async def test_local_seed_refuses_non_development_environment() -> None:
    with (
        patch.dict(os.environ, {"WOS_ENVIRONMENT": "production"}),
        pytest.raises(RuntimeError, match="restricted to development"),
    ):
        await local_smoke_seed.seed()


def test_tracker_fixture_refuses_non_development_environment() -> None:
    with (
        patch.dict(os.environ, {"WOS_ENVIRONMENT": "production"}),
        pytest.raises(RuntimeError, match="restricted to development"),
    ):
        local_tracker_fixture.main()


def test_local_smoke_script_contains_no_versioned_credentials() -> None:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/smoke_v2_local.py"))
    assert namespace["ROOT"] == repository
    assert "password" not in namespace


def test_local_smoke_fixture_is_a_valid_c411_torrent() -> None:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/smoke_v2_local.py"))
    content, expected_info_hash, file_name = namespace["fixture"]()

    parsed = sanitize_torrent(
        content,
        allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
        max_total_size=1024,
    )

    assert parsed.info_hash == expected_info_hash
    assert parsed.total_size == 1
    assert parsed.name == file_name


def test_local_smoke_covers_ready_downloads_and_retained_cancellation() -> None:
    repository = Path(__file__).resolve().parents[2]
    script = (repository / "scripts/smoke_v2_local.py").read_text()

    assert 'headers={"Range": "bytes=0-0"}' in script
    assert "download-archive?snapshot=" in script
    assert 'method="DELETE"' in script
    assert "PURGE_PENDING|CANCELLED|QUEUED" in script
    assert 'f"{base}/api/v2/metrics"' in script
    assert "secret_safe_metrics_checked" in script
