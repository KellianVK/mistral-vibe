from __future__ import annotations

import atexit
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from threading import Lock, Thread
from typing import Any, TypedDict, cast
from urllib.parse import parse_qs, urlsplit
import webbrowser

from vibe.core.logger import logger
from vibe.core.utils.io import read_safe

STATIC_DIR = Path(__file__).resolve().parent / "static"
WORKFLOW_MODEL = "mistral-medium-3.5"
ASSETS = {
    "/assets/dashboard.css": ("assets/dashboard.css", "text/css; charset=utf-8"),
    "/assets/dashboard.js": ("assets/dashboard.js", "text/javascript; charset=utf-8"),
}

type StatusProvider = Callable[[], Mapping[str, Any]]
type BrowserOpener = Callable[[str], bool]


class DashboardLaunch(TypedDict):
    url: str
    browser_opened: bool


class WorkflowDashboardError(RuntimeError):
    pass


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, token: str, status_provider: StatusProvider, static_dir: Path
    ) -> None:
        self.token = token
        self.status_provider = status_provider
        self.static_dir = static_dir
        super().__init__(("127.0.0.1", 0), _DashboardRequestHandler)


class _DashboardRequestHandler(BaseHTTPRequestHandler):
    @property
    def dashboard_server(self) -> _DashboardHTTPServer:
        return cast(_DashboardHTTPServer, self.server)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/favicon.ico":
            self._respond(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            return
        if request.path in ASSETS:
            relative_path, content_type = ASSETS[request.path]
            self._serve_file(relative_path, content_type)
            return
        if not self._is_authorized(request.query):
            self._respond(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        if request.path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if request.path == "/api/status":
            self._serve_status()
            return
        self._respond(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def _is_authorized(self, query: str) -> bool:
        supplied = parse_qs(query).get("token", [""])[0]
        return secrets.compare_digest(supplied, self.dashboard_server.token)

    def _serve_file(self, relative_path: str, content_type: str) -> None:
        path = self.dashboard_server.static_dir / relative_path
        try:
            content = read_safe(path).text.encode()
        except OSError:
            self._respond(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        self._respond(HTTPStatus.OK, content, content_type)

    def _serve_status(self) -> None:
        try:
            payload = {
                **self.dashboard_server.status_provider(),
                "model": WORKFLOW_MODEL,
                "generated_at": datetime.now(UTC).isoformat(),
            }
            content = json.dumps(payload, ensure_ascii=False).encode()
        except Exception as error:
            logger.error("Workflow dashboard status failed: %s", error)
            content = b'{"error":"Could not load workflow status"}'
            self._respond(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                content,
                "application/json; charset=utf-8",
            )
            return
        self._respond(HTTPStatus.OK, content, "application/json; charset=utf-8")

    def _respond(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:",
        )
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


class WorkflowDashboardServer:
    def __init__(
        self, status_provider: StatusProvider, *, static_dir: Path = STATIC_DIR
    ) -> None:
        self._status_provider = status_provider
        self._static_dir = static_dir
        self._lock = Lock()
        self._server: _DashboardHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> str:
        with self._lock:
            if self._server is None:
                missing_assets = [
                    relative_path
                    for relative_path in [
                        "index.html",
                        "assets/dashboard.css",
                        "assets/dashboard.js",
                    ]
                    if not (self._static_dir / relative_path).is_file()
                ]
                if missing_assets:
                    missing = ", ".join(missing_assets)
                    raise WorkflowDashboardError(
                        f"Workflow dashboard assets are missing: {missing}"
                    )
                self._server = _DashboardHTTPServer(
                    secrets.token_urlsafe(24), self._status_provider, self._static_dir
                )
                self._thread = Thread(
                    target=self._server.serve_forever,
                    name="vibe-workflow-dashboard",
                    daemon=True,
                )
                self._thread.start()
            host, port = cast(tuple[str, int], self._server.server_address)
            return f"http://{host}:{port}/?token={self._server.token}"

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)


_dashboards: dict[Path, WorkflowDashboardServer] = {}
_dashboards_lock = Lock()


def open_workflow_dashboard(
    workdir: Path,
    status_provider: StatusProvider,
    *,
    browser_opener: BrowserOpener = webbrowser.open,
) -> DashboardLaunch:
    resolved_workdir = workdir.expanduser().resolve()
    with _dashboards_lock:
        server = _dashboards.get(resolved_workdir)
        if server is None:
            server = WorkflowDashboardServer(status_provider)
            _dashboards[resolved_workdir] = server
    url = server.start()
    try:
        browser_opened = browser_opener(url)
    except Exception as error:
        logger.debug("Failed to open workflow dashboard url=%s: %s", url, error)
        browser_opened = False
    return DashboardLaunch(url=url, browser_opened=browser_opened)


def close_workflow_dashboards() -> None:
    with _dashboards_lock:
        servers = list(_dashboards.values())
        _dashboards.clear()
    for server in servers:
        server.close()


atexit.register(close_workflow_dashboards)
