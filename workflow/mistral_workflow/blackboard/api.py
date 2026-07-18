import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from mistral_workflow.blackboard.store import Blackboard, blackboard_path
from mistral_workflow.roles import load_manifest, manifest_path

POLL_INTERVAL_SEC = 1.0


def create_app(project_dir: Path) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    blackboard = Blackboard(blackboard_path(project_dir))

    @app.get("/state")
    def get_state() -> dict:
        return blackboard.state()

    @app.get("/manifest")
    def get_manifest() -> dict:
        path = manifest_path(project_dir)
        if not path.exists():
            return {"project": None, "roles": []}
        manifest = load_manifest(path)
        return manifest.model_dump()

    @app.websocket("/ws")
    async def ws_state(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sent: str | None = None
        try:
            while True:
                serialized = json.dumps(blackboard.state(), sort_keys=True)
                if serialized != last_sent:
                    await websocket.send_text(serialized)
                    last_sent = serialized
                await asyncio.sleep(POLL_INTERVAL_SEC)
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    import uvicorn

    project_dir = Path(os.environ.get("MISTRAL_WORKFLOW_PROJECT_DIR", ".")).resolve()
    app = create_app(project_dir)
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
