import runpy
from pathlib import Path
from typing import Any

import pytest


def _policy() -> tuple[type[RuntimeError], Any]:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/rise2_v2_newgreedy_policy.py"))
    return namespace["NewGreedyPolicyError"], namespace["validate_config"]


def _write_config(path: Path, *, flow_detail: str | None = "0") -> None:
    proxy_lines = ["[proxy]", "listen_port = 3456"]
    if flow_detail is not None:
        proxy_lines.append(f"flow_detail = {flow_detail}")
    path.write_text(
        "\n".join(
            [
                *proxy_lines,
                "",
                "[stats]",
                "persist_stats = true",
                "auto_purge_stopped = false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_newgreedy_policy_accepts_secret_safe_logging(tmp_path: Path) -> None:
    _, validate = _policy()
    config = tmp_path / "config.ini"
    _write_config(config, flow_detail="0 ; do not log full flows")

    validate(config)


@pytest.mark.parametrize("flow_detail", [None, "1", "2", "-1", "not-an-int"])
def test_newgreedy_policy_rejects_missing_or_verbose_flow_detail(
    tmp_path: Path, flow_detail: str | None
) -> None:
    error, validate = _policy()
    config = tmp_path / "config.ini"
    _write_config(config, flow_detail=flow_detail)

    with pytest.raises(error):
        validate(config)


def test_newgreedy_policy_rejects_unsafe_stats_policy(tmp_path: Path) -> None:
    error, validate = _policy()
    config = tmp_path / "config.ini"
    _write_config(config)
    content = config.read_text(encoding="utf-8").replace(
        "auto_purge_stopped = false", "auto_purge_stopped = true"
    )
    config.write_text(content, encoding="utf-8")

    with pytest.raises(error):
        validate(config)


def test_rise2_compose_enforces_policy_before_newgreedy_start() -> None:
    repository = Path(__file__).resolve().parents[2]
    compose = (repository / "deploy/compose.rise2.v2.yaml").read_text(encoding="utf-8")
    init_block = compose.split("\n  newgreedy-init:\n", 1)[1].split("\n  newgreedy:\n", 1)[0]

    assert "source: ../scripts/rise2_v2_newgreedy_policy.py" in init_block
    assert "target: /bootstrap/newgreedy-policy.py" in init_block
    assert "target: /bootstrap/config.ini" in init_block
    assert "python3 /bootstrap/newgreedy-policy.py /bootstrap/config.ini" in init_block
    assert "group_add:" in init_block
    assert "WOS_V2_NEWGREEDY_GID" in init_block
    assert "newgreedy-init:\n        condition: service_completed_successfully" in compose


def test_rise2_runtime_smoke_covers_flow_detail_policy() -> None:
    repository = Path(__file__).resolve().parents[2]
    smoke = (repository / "scripts/rise2_v2_newgreedy_smoke.sh").read_text(encoding="utf-8")

    assert "flow_detail = 0" in smoke
    assert "flow_detail = 1" in smoke
    assert "verbose flow_detail unexpectedly passed newgreedy-init" in smoke
