from pathlib import Path


def _frontend_source(path: str) -> str:
    repository = Path(__file__).resolve().parents[2]
    return (repository / "frontend" / "src" / path).read_text(encoding="utf-8")


def test_authenticated_navigation_keeps_the_browser_download_manager_mounted() -> None:
    app = _frontend_source("App.tsx")
    downloads = _frontend_source("features/torrents/UserDownloadsPage.tsx")

    assert '<main className="app-shell" hidden={hidden}>' in app
    assert '<div hidden={view !== "dashboard"}>' in app
    assert "<UserDashboardPage onSessionExpired={onSessionExpired} />" in app
    assert "hidden={legalDocument !== null}" in app
    assert 'auth.status === "authenticated" && !auth.user.must_change_credentials' in app

    # The manager must only be disposed when the authenticated dashboard itself unmounts
    # (logout/session loss), not when Settings/Admin/Legal merely hide that dashboard.
    assert "useEffect(() => () => managerRef.current?.dispose(), []);" in downloads
