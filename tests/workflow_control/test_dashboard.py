from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx

from workflow_control._dashboard import (
    WorkflowDashboardServer,
    close_workflow_dashboards,
    open_workflow_dashboard,
)


def _snapshot() -> dict[str, object]:
    return {
        "state": "running",
        "goal": "Build a game",
        "error": None,
        "agents": [
            {
                "role": "Backend",
                "state": "working",
                "current_task": "Implement gameplay",
                "updated_at": "2026-07-18T12:26:06.770035+00:00",
                "decision_count": 2,
            }
        ],
    }


def test_dashboard_server_serves_assets_and_token_protected_status(
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<main>dashboard</main>", encoding="utf-8")
    (assets_dir / "dashboard.js").write_text("export {};", encoding="utf-8")
    (assets_dir / "dashboard.css").write_text("body {}", encoding="utf-8")
    server = WorkflowDashboardServer(_snapshot, static_dir=static_dir)

    try:
        url = server.start()
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with httpx.Client(trust_env=False) as client:
            index = client.get(url)
            status = client.get(f"{origin}/api/status?{parsed.query}")
            unauthorized = client.get(f"{origin}/api/status")
            asset = client.get(f"{origin}/assets/dashboard.js")

        assert index.text == "<main>dashboard</main>"
        assert index.headers["content-security-policy"].startswith("default-src 'none'")
        assert status.json() == {
            **_snapshot(),
            "model": "mistral-medium-3.5",
            "generated_at": status.json()["generated_at"],
        }
        assert unauthorized.status_code == 404
        assert asset.text == "export {};"
        assert server.start() == url
    finally:
        server.close()


def test_open_workflow_dashboard_reuses_server_and_opens_browser(
    tmp_path: Path,
) -> None:
    opened_urls: list[str] = []

    try:
        first = open_workflow_dashboard(
            tmp_path,
            _snapshot,
            browser_opener=lambda url: opened_urls.append(url) or True,
        )
        second = open_workflow_dashboard(
            tmp_path,
            _snapshot,
            browser_opener=lambda url: opened_urls.append(url) or True,
        )

        assert first == {"url": opened_urls[0], "browser_opened": True}
        assert second == {"url": opened_urls[1], "browser_opened": True}
        assert first["url"] == second["url"]
    finally:
        close_workflow_dashboards()
