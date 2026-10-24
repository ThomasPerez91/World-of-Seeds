from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_static_files_revalidate_pages_and_cache_fingerprinted_bundles(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (static / "assets" / "index-3f9a1c.js").write_text("console.log(1)", encoding="utf-8")
    (static / "branding-lock.css").write_text("body{}", encoding="utf-8")
    application = create_app(Settings(data_root=tmp_path, static_root=static))

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://localhost"
    ) as client:
        page = await client.get("/")
        bundle = await client.get("/assets/index-3f9a1c.js")
        unhashed = await client.get("/branding-lock.css")
        missing = await client.get("/assets/absent.js")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache"
    assert bundle.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert unhashed.headers["cache-control"] == "no-cache"
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("cache-control", "")
