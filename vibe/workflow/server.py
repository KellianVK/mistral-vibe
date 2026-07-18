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
            "messages": [],
            "broadcasts": [],
            "changes": [],
            "timings": {},
        }
    return read_board_state(database_path)


def _role_logs(workdir: Path, role: str) -> list[dict[str, Any]]:
    """Parsed NDJSON transcript of one worker, trimmed for the Logs tab."""
    safe_role = "".join(ch for ch in role.lower() if ch.isalnum() or ch in "-_")
    log_path = workdir / "logs" / f"{safe_role}.jsonl"
    if not log_path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("role") == "system":
            continue
        tool_calls = message.get("tool_calls") or []
        entries.append({
            "role": message.get("role"),
            "content": (message.get("content") or "")[:2000],
            "tools": [
                call.get("function", {}).get("name")
                for call in tool_calls
                if isinstance(call, dict)
            ],
            "tool_name": message.get("name"),
        })
    return entries


def _system_info(workdir: Path) -> dict[str, Any]:
    """What MiaouFlow provisioned: agent profiles, tools, prompts, db."""
    from vibe.core.tools.builtins import blackboard

    agents_dir = workdir / ".vibe" / "agents"
    profiles: list[dict[str, str]] = []
    if agents_dir.is_dir():
        for profile in sorted(agents_dir.glob("*.toml")):
            profiles.append({
                "name": profile.stem,
                "content": profile.read_text(encoding="utf-8"),
            })

    tool_classes = [
        blackboard.PublishDecision,
        blackboard.ReadDecisions,
        blackboard.UpdateStatus,
        blackboard.RequestReview,
        blackboard.AnswerQuestion,
        blackboard.ReadQuestions,
        blackboard.ClaimFile,
        blackboard.ReleaseFile,
        blackboard.SendMessage,
        blackboard.Broadcast,
        blackboard.ReadInbox,
    ]
    tools = [
        {"name": cls.get_name(), "description": cls.description} for cls in tool_classes
    ]

    prompts_dir = Path(__file__).resolve().parent / "prompts"
    prompts = sorted(p.stem for p in prompts_dir.glob("*.md"))
    return {
        "database": str(workflow_database_path(workdir)),
        "agent_profiles": profiles,
        "blackboard_tools": tools,
        "role_prompts": prompts,
    }


def _register_agent(workdir: Path, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    from vibe.workflow.roles import RoleSpec, save_custom_role
    from vibe.workflow.setup import configure_workdir

    name = str(body.get("name") or "").strip()
    objective = str(body.get("objective") or "").strip()
    if not name or not objective:
        return {"error": "name and objective are required"}, 400
    role = RoleSpec(
        name=name,
        agent_profile="".join(c for c in name.lower() if c.isalnum() or c == "-")
        or "custom",
        model=str(body.get("model") or "devstral-small"),
        objective=objective,
        depends_on=tuple(body.get("depends_on") or ("Planner",)),
    )
    try:
        save_custom_role(workdir, role)
        configure_workdir(workdir, [role])
    except Exception as error:
        return {"error": str(error)}, 409
    return (
        {
            "ok": True,
            "role": role.name,
            "note": "The agent joins the team on the next run.",
        },
        201,
    )


def _manifest(workdir: Path) -> dict[str, Any]:
    state = _board_state(workdir)
    run = state.get("run")
    if run is None:
        return {"project": None, "roles": []}

    from vibe.workflow.roles import load_custom_roles

    custom = load_custom_roles(workdir)
    agents: dict[str, Any] = state.get("agents", {})
    active = set(agents) | set(DEFAULT_ACTIVE_ROLES) | {r.name for r in custom}
    roles = [role for role in (*DEFAULT_ROLES, *custom) if role.name in active]
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

    async def get_logs(request: Request) -> JSONResponse:
        role = request.query_params.get("role", "")
        if not role:
            return JSONResponse({"error": "role query param required"}, status_code=400)
        entries = await asyncio.to_thread(_role_logs, resolved_workdir, role)
        return JSONResponse({"role": role, "entries": entries})

    async def get_system(request: Request) -> JSONResponse:
        del request
        return JSONResponse(await asyncio.to_thread(_system_info, resolved_workdir))

    async def get_change(request: Request) -> JSONResponse:
        from vibe.workflow.store import read_change

        raw_id = request.query_params.get("id", "")
        if not raw_id.isdigit():
            return JSONResponse({"error": "numeric id required"}, status_code=400)
        database_path = workflow_database_path(resolved_workdir)
        if not database_path.exists():
            return JSONResponse({"error": "no workflow database"}, status_code=404)
        change = await asyncio.to_thread(read_change, database_path, int(raw_id))
        if change is None:
            return JSONResponse({"error": "unknown change id"}, status_code=404)
        return JSONResponse(dict(change))

    async def post_agent(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        payload, status_code = await asyncio.to_thread(
            _register_agent, resolved_workdir, body
        )
        return JSONResponse(payload, status_code=status_code)

    routes: list[Route | WebSocketRoute | Mount] = [
        Route("/state", get_state),
        Route("/manifest", get_manifest),
        Route("/logs", get_logs),
        Route("/system", get_system),
        Route("/change", get_change),
        Route("/agents", post_agent, methods=["POST"]),
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
