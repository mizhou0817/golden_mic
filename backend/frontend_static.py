"""Static frontend with an explicit, task-independent SPA document allowlist."""
from __future__ import annotations

import re

from starlette._utils import get_route_path
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


# Keep aligned with Workspace.readRoute and appApi.validTaskId. A syntactically
# valid ID is NOT an authorization/existence check: the UI must still call API.
_TASK_DOCUMENT = re.compile(r"/tasks/[A-Za-z0-9_-]{1,128}/?")


def _unsafe_path(path: str) -> bool:
    # Validate BEFORE StaticFiles' normalized path is used for any lookup.
    # Canonical UI/build URLs need no percent escapes. Reject encoded aliases
    # too, including double encoding, rather than interpreting them differently
    # from the browser's location.pathname. Query strings are never inspected.
    if not path.startswith("/") or "//" in path:
        return True
    if any(ord(char) < 32 or ord(char) == 127 or char in '<>:"|?*\\%' for char in path):
        return True
    return any(part.startswith(".") or part.endswith((".", " ")) for part in path.split("/") if part)


class FrontendStaticFiles(StaticFiles):
    """Serve unchanged index bytes only for known UI document routes.

    No settings, task storage, authentication or pipeline imports. API/health
    routes must be registered before this mount; unmatched reserved paths stay
    404 even for non-read methods and even if such files exist in the build.
    FileResponse retains HEAD, conditional requests, range and MIME behavior.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        route_path = get_route_path(scope)
        raw_path = scope.get("raw_path", b"").split(b"?", 1)[0]
        if _unsafe_path(route_path) or (raw_path and _unsafe_path(raw_path.decode("latin1"))):
            raise HTTPException(status_code=404)
        if route_path.split("/", 2)[1] in {"api", "health"}:
            raise HTTPException(status_code=404)
        if route_path == "/samples/default" or _TASK_DOCUMENT.fullmatch(route_path):
            # Select the fixed public document, never a tasks/<id> filesystem
            # path. Delegate method handling (405) and response semantics.
            return await super().get_response("index.html", scope)
        return await super().get_response(path, scope)