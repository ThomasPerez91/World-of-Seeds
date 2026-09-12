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


def test_dashboard_title_uses_normal_page_background_in_every_theme() -> None:
    styles = (REPOSITORY / "frontend/src/wos-2-1.css").read_text()
    review_fixes = (REPOSITORY / "frontend/src/wos-premium-review-fixes.css").read_text()
    rule_start = styles.index(".user-dashboard-header {")
    rule = styles[rule_start : styles.index("}", rule_start) + 1]

    assert "min-height: 0 !important" in rule
    assert "padding: 0 !important" in rule
    assert "background: none !important" in rule
    assert "url(" not in rule
    assert "linear-gradient" not in rule
    assert ".dashboard-summary-grid { margin-top: -" not in styles
    assert ".user-dashboard-header" not in review_fixes


def test_admin_finish_is_mobile_first_and_legacy_user_filesystem_ui_is_absent() -> None:
    admin_styles = (REPOSITORY / "frontend/src/features/admin/admin.css").read_text()
    shell = (REPOSITORY / "frontend/src/features/admin/AdminPageShell.tsx").read_text()
    settings_shell = (REPOSITORY / "frontend/src/components/SettingsShell.tsx").read_text()
    app = (REPOSITORY / "frontend/src/App.tsx").read_text()
    client = (REPOSITORY / "frontend/src/api/client.ts").read_text()

    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in admin_styles
    assert "@media (min-width: 600px)" in admin_styles
    assert "@media (min-width: 900px)" in admin_styles
    assert "overflow-wrap: anywhere" in admin_styles
    assert "min-height: 2.75rem" in admin_styles
    assert "<SettingsShell" in shell
    assert 'aria-current={activeView === item.view ? "page" : undefined}' in settings_shell
    assert '"admin-trash"' not in shell
    assert "AdminTrashPage" not in app
    assert 't("account.renameHint")' not in app
    assert "listAdminTrash" not in client
    assert "purgeAdminTrash" not in client
    assert "purgeAllAdminTrash" not in client
    assert "listFiles" not in client
    assert not (REPOSITORY / "frontend/src/features/files").exists()
    assert not (REPOSITORY / "frontend/src/api/storage.ts").exists()
    assert not (REPOSITORY / "backend/app/files").exists()
    assert not (REPOSITORY / "backend/app/schemas/files.py").exists()
    assert not (REPOSITORY / "backend/app/schemas/torrents.py").exists()
    styles = (REPOSITORY / "frontend/src/styles.css").read_text()
    translations = (REPOSITORY / "frontend/src/i18n.tsx").read_text()
    assert ".admin-trash" not in styles
    assert "error.workspaceUnavailable" not in translations
    assert "admin.trashItems" not in translations


def test_review_fixes_share_one_transparent_settings_shell_and_center_tree_rows() -> None:
    styles = (REPOSITORY / "frontend/src/wos-2-1-final.css").read_text()
    downloads = (
        REPOSITORY / "frontend/src/features/torrents/UserDownloadsPage.tsx"
    ).read_text()
    storage = (REPOSITORY / "frontend/src/features/admin/AdminStoragePage.tsx").read_text()

    assert ".settings-shell-content > .admin-section" in styles
    assert ".settings-shell-content > .settings-panel" in styles
    assert "background: transparent !important" in styles
    assert ".settings-shell-navigation-item[aria-current=\"page\"]" in styles
    assert ".options-summary-content" in styles
    assert ".c411-account-fields" in styles
    assert "grid-template-columns: minmax(0, 3fr) minmax(8rem, 1.7fr)" in styles
    assert ".ready-directory-root-label" in styles
    assert "<span className=\"ready-directory-root-label\">" in downloads
    assert "getAdminReconciliation" not in storage


def test_windows_favicon_contract_is_valid_and_cache_busted() -> None:
    html = (REPOSITORY / "frontend/index.html").read_text()
    icon = (REPOSITORY / "frontend/public/favicon-v2.ico").read_bytes()

    assert 'rel="icon" type="image/svg+xml" href="/favicon-v2.svg"' in html
    assert 'rel="icon" type="image/x-icon" href="/favicon-v2.ico"' in html
    assert 'rel="shortcut icon" href="/favicon-v2.ico"' in html
    assert icon[:6] == bytes((0, 0, 1, 0, 3, 0))

    sizes: list[int] = []
    for index in range(3):
        entry = 6 + index * 16
        width = icon[entry] or 256
        height = icon[entry + 1] or 256
        byte_count = int.from_bytes(icon[entry + 8 : entry + 12], "little")
        offset = int.from_bytes(icon[entry + 12 : entry + 16], "little")
        dib_size = int.from_bytes(icon[offset : offset + 4], "little")
        dib_width = int.from_bytes(icon[offset + 4 : offset + 8], "little", signed=True)
        dib_height = int.from_bytes(icon[offset + 8 : offset + 12], "little", signed=True)
        planes = int.from_bytes(icon[offset + 12 : offset + 14], "little")
        bits_per_pixel = int.from_bytes(icon[offset + 14 : offset + 16], "little")

        assert width == height
        assert offset + byte_count <= len(icon)
        assert dib_size == 40
        assert dib_width == width
        assert dib_height == height * 2
        assert planes == 1
        assert bits_per_pixel == 32
        sizes.append(width)

    assert sizes == [16, 32, 48]
