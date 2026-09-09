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


def test_admin_finish_is_mobile_first_and_legacy_user_filesystem_ui_is_absent() -> None:
    admin_styles = (REPOSITORY / "frontend/src/features/admin/admin.css").read_text()
    shell = (REPOSITORY / "frontend/src/features/admin/AdminPageShell.tsx").read_text()
    app = (REPOSITORY / "frontend/src/App.tsx").read_text()
    client = (REPOSITORY / "frontend/src/api/client.ts").read_text()

    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in admin_styles
    assert "@media (min-width: 600px)" in admin_styles
    assert "@media (min-width: 900px)" in admin_styles
    assert "overflow-wrap: anywhere" in admin_styles
    assert "min-height: 2.75rem" in admin_styles
    assert 'aria-current={activeView === item.view ? "page" : undefined}' in shell
    assert '"admin-trash"' not in shell
    assert "AdminTrashPage" not in app
    assert 't("account.renameHint")' not in app
    assert "listAdminTrash" not in client
    assert "purgeAdminTrash" not in client
    assert "purgeAllAdminTrash" not in client
    assert "listFiles" not in client
    assert not (REPOSITORY / "frontend/src/features/files").exists()
