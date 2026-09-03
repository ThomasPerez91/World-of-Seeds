import runpy
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY / "scripts/rise2_v2_qb_bootstrap.py"


def module() -> dict[str, Any]:
    return runpy.run_path(str(BOOTSTRAP))


def test_qb_queueing_caps_seeders_without_competing_with_wos_download_slots() -> None:
    ns = module()
    expected = {
        ("BitTorrent", r"Session\QueueingSystemEnabled"): "true",
        ("BitTorrent", r"Session\MaxActiveDownloads"): "-1",
        ("BitTorrent", r"Session\MaxActiveUploads"): "1000",
        ("BitTorrent", r"Session\MaxActiveTorrents"): "-1",
        ("BitTorrent", r"Session\IgnoreSlowTorrentsForQueueing"): "false",
    }

    policy = ns["settings"](ns["POLICY"].read_text(encoding="utf-8"))
    for key, value in expected.items():
        assert ns["REQUIRED"][key] == value
        assert policy[key] == value

    rendered = ns["render"](
        "[BitTorrent]\n"
        "Session\\QueueingSystemEnabled=true\n"
        "Session\\MaxActiveDownloads=1\n"
        "Session\\MaxActiveUploads=0\n"
        "Session\\MaxActiveTorrents=1\n"
        "Session\\IgnoreSlowTorrentsForQueueing=true\n"
        "General\\Unrelated=true\n",
        "test-user",
        "disposable-test-password-for-seeding",
    )
    settings = ns["settings"](rendered)
    for key, value in expected.items():
        assert settings[key] == value
    assert settings[("BitTorrent", r"General\Unrelated")] == "true"
