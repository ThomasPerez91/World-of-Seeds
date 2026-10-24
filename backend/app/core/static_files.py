"""Static front-end files with cache rules that survive a deployment."""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

IMMUTABLE_PREFIX = "assets/"


class DeploymentAwareStaticFiles(StaticFiles):
    """Fingerprinted bundles are immutable; every other file is revalidated each time.

    Without a freshness directive a browser applies a heuristic lifetime to ``index.html``: after
    a deployment it can keep serving a page that references bundles which no longer exist.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            immutable = path.replace("\\", "/").startswith(IMMUTABLE_PREFIX)
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if immutable else "no-cache"
            )
        return response
