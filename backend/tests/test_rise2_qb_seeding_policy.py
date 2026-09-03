import runpy
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY / "scripts/rise2_v2_qb_bootstrap.py"


def module() -> dict[str, Any]:
    return runpy.run_path(str(BOOTSTRAP))


def test_qb_queueing_is_disabled_so_wos_scheduler_remains_authoritative() -> None:
    ns = module()
    key = ("BitTorrent", r"Session\QueueingSystemEnabled")

    assert ns["REQUIRED"][key] == "false"
    assert ns["settings"](ns["POLICY"].read_text(encoding="utf-8"))[key] == "false"

    rendered = ns["render"](
        "[BitTorrent]\nSession\\QueueingSystemEnabled=true\nGeneral\\Unrelated=true\n",
        "test-user",
        "disposable-test-password-for-seeding",
    )
    settings = ns["settings"](rendered)
    assert settings[key] == "false"
    assert settings[("BitTorrent", r"General\Unrelated")] == "true"
