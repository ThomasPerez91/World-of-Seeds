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
    assert ".header-actions" in styles
    assert ".language-selector" in styles
    assert "flex-wrap: wrap" in styles


def test_torrent_manager_uses_native_accordions_without_duplicate_mobile_markup() -> None:
    page = (REPOSITORY / "frontend/src/features/torrents/UserDownloadsPage.tsx").read_text()

    assert '<ul className="torrent-accordion-list"' in page
    assert "<Accordion" in page
    assert 'className="torrent-accordion-summary"' in page
    assert 'className="torrent-card-actions"' in page
    assert '<table className="torrent-table">' not in page
    assert 'data-label={t("' not in page
    assert "window.location.reload" not in page
