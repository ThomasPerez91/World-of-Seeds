from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


def test_responsive_styles_cover_supported_mobile_and_orientation_contract() -> None:
    styles = (REPOSITORY / "frontend/src/styles.css").read_text()

    assert "min-width: 320px" in styles
    assert "@media (max-width: 680px)" in styles
    assert "@media (max-width: 820px) and (orientation: portrait)" in styles
    assert "@media (max-width: 1024px) and (orientation: landscape)" in styles
    assert "content: attr(data-label)" in styles
    assert "max-height: calc(100dvh - 1rem)" in styles
    assert "env(safe-area-inset-bottom)" in styles


def test_torrent_table_exposes_card_labels_without_duplicate_mobile_markup() -> None:
    page = (REPOSITORY / "frontend/src/features/torrents/UserDownloadsPage.tsx").read_text()

    for label in ("Nom", "État", "Taille", "Progression", "Mise à jour", "Actions"):
        assert f'data-label="{label}"' in page
    assert "window.location.reload" not in page
