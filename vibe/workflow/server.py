"""MiaouFlow board server: state API + WebSocket + static board UI.

Plain Starlette (already a Vibe dependency) — no FastAPI. Serves the built
visualizer from ``board_dist/`` so a demo machine needs no Node toolchain.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

from vibe.workflow.roles import DEFAULT_ACTIVE_ROLES, DEFAULT_ROLES, manifest_payload
from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import read_board_state

POLL_INTERVAL_SEC = 1.0
BOARD_DIST_DIR = Path(__file__).resolve().parent / "board_dist"

_MISSING_BOARD_PAGE = """<!doctype html>
<html><body style="font-family: monospace; background: #121212; color: #c5c8c6">
<h1 style="color:#ff8205">MiaouFlow</h1>
<p>The board UI has not been built. Run:</p>
<pre>cd visualizer && npm ci && npm run build
cp -r dist/* ../vibe/workflow/board_dist/</pre>
<p>The state API is live at <a href="/state" style="color:#ff8205">/state</a>.</p>
</body></html>"""


def _board_state(workdir: Path) -> dict[str, Any]:
    database_path = workflow_database_path(workdir)
    if not database_path.exists():
        return {
            "goal": None,
            "run": None,
            "agents": {},
            "decisions": [],
            "questions": [],
            "claims": [],
            "timings": {},
        }
    return read_board_state(database_path)


def _manifest(workdir: Path) -> dict[str, Any]:
    state = _board_state(workdir)
    run = state.get("run")
    if run is None:
        return {"project": None, "roles": []}

    agents: dict[str, Any] = state.get("agents", {})
    active = set(agents) | set(DEFAULT_ACTIVE_ROLES)
    roles = [role for role in DEFAULT_ROLES if role.name in active]
    return {
        "project": {"goal": run["goal"], "gates": [], "max_loop_iterations": 1},
        "roles": manifest_payload(roles),
    }


def create_app(workdir: Path) -> Starlette:
    resolved_workdir = workdir.expanduser().resolve()

    async def get_state(request: Request) -> JSONResponse:
        del request
        return JSONResponse(await asyncio.to_thread(_board_state, resolved_workdir))

    async def get_manifest(request: Request) -> JSONResponse:
        del request
        return JSONResponse(await asyncio.to_thread(_manifest, resolved_workdir))

    async def ws_state(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sent: str | None = None
        try:
            while True:
                state = await asyncio.to_thread(_board_state, resolved_workdir)
                serialized = json.dumps(state, sort_keys=True)
                if serialized != last_sent:
                    await websocket.send_text(serialized)
                    last_sent = serialized
                await asyncio.sleep(POLL_INTERVAL_SEC)
        except (WebSocketDisconnect, RuntimeError):
            return

    async def board_placeholder(request: Request) -> HTMLResponse:
        del request
        return HTMLResponse(_MISSING_BOARD_PAGE)

    routes: list[Route | WebSocketRoute | Mount] = [
        Route("/state", get_state),
        Route("/manifest", get_manifest),
        WebSocketRoute("/ws", ws_state),
    ]
    if BOARD_DIST_DIR.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=BOARD_DIST_DIR, html=True)))
    else:
        routes.append(Route("/", board_placeholder))

    middleware = [
        Middleware(
            CORSMiddleware,
            # 5173 = `vite` dev server, 4173 = `vite preview`. When the board
            # is served from this same origin, CORS never applies.
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:4173",
                "http://127.0.0.1:4173",
            ],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
    return Starlette(routes=routes, middleware=middleware)


async def serve_async(workdir: Path, port: int) -> None:
    config = uvicorn.Config(
        create_app(workdir), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()


def serve_board(workdir: Path, *, port: int) -> int:
    print(f"MiaouFlow board: http://127.0.0.1:{port} (Ctrl-C to exit)")
    try:
        asyncio.run(serve_async(workdir, port))
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    workdir = Path(os.environ.get("VIBE_WORKFLOW_WORKDIR", ".")).resolve()
    port = int(os.environ.get("VIBE_WORKFLOW_BOARD_PORT", "8787"))
    raise SystemExit(serve_board(workdir, port=port))


if __name__ == "__main__":
    main()
