from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_scheduler_load.py"
    spec = importlib.util.spec_from_file_location("rise2_v2_scheduler_load", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load = _load_module()


def test_campaign_validation_is_bounded() -> None:
    assert load._campaign("g3-20260904") == "g3-20260904"
    for value in ("", "UPPER", "bad_name", "x" * 17):
        with pytest.raises(ValueError):
            load._campaign(value)


def test_scheduler_fixtures_are_deterministic_and_isolated() -> None:
    fixtures = load._fixtures("g3-test", 3)
    repeated = load._fixtures("g3-test", 3)

    assert len(fixtures) == load.TOTAL_TORRENT_COUNT == 209
    assert [item.info_hash for item in fixtures] == [item.info_hash for item in repeated]
    assert len({item.info_hash for item in fixtures}) == len(fixtures)
    assert len({item.storage_key for item in fixtures}) == len(fixtures)
    assert {item.route_index for item in fixtures} == {0, 1, 2}
    assert all(b"127.0.0.1:1/announce" in item.metainfo for item in fixtures)
    assert all(b"c411" not in item.metainfo.lower() for item in fixtures)

    assert sum(item.kind == "eligible" for item in fixtures) == 205
    assert sum(item.kind == "cooldown" for item in fixtures) == 2
    assert sum(item.kind == "ready" for item in fixtures) == 2


def test_scheduler_fixture_sizes_cover_all_policy_classes() -> None:
    fixtures = load._fixtures("g3-size", 1)
    sizes = [item.scheduler_size for item in fixtures]

    assert max(sizes) > 53_687_091_200
    assert any(10_737_418_240 < size <= 53_687_091_200 for size in sizes)
    assert any(size <= 10_737_418_240 for size in sizes)


def test_percentile_uses_bounded_observed_sample() -> None:
    assert load._percentile([], 0.95) == 0.0
    assert load._percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 3.0


def test_production_runtime_wrapper_forces_real_redis() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "rise2_v2_scheduler_load_runtime.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "RedisCoordinator.from_settings(get_settings())" in source
    assert 'kwargs["redis"] = redis' in source
    assert "await redis.aclose()" in source


@pytest.mark.asyncio
async def test_load_gate_refuses_short_measurements_before_touching_runtime() -> None:
    with pytest.raises(ValueError, match="at least 300s warmup and 1800s measurement"):
        await load.run_load(
            "g3-test",
            slots=1,
            warmup_seconds=299,
            measurement_seconds=1800,
        )


@pytest.mark.asyncio
async def test_load_gate_refuses_unknown_slot_count_before_touching_runtime() -> None:
    with pytest.raises(ValueError, match="slots must be 1 or 2"):
        await load.run_load(
            "g3-test",
            slots=3,
            warmup_seconds=300,
            measurement_seconds=1800,
        )
