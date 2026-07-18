from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from setup_workflow import (
    WorkflowConfigurationError,
    configure_workdir,
    render_mcp_config,
    workflow_database_path,
)


def test_configure_workdir_preserves_existing_config_and_is_idempotent(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    config_path = workdir / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    existing = (
        "# Keep this project configuration.\n"
        'active_model = "local"\n'
        "\n"
        "[[mcp_servers]]\n"
        'name = "existing"\n'
        'transport = "http"\n'
        'url = "http://127.0.0.1:9000"\n'
    )
    config_path.write_text(existing, encoding="utf-8")

    assert configure_workdir(workdir) == config_path
    first_render = config_path.read_text(encoding="utf-8")
    assert first_render.startswith(existing)

    assert configure_workdir(workdir) == config_path
    assert config_path.read_text(encoding="utf-8") == first_render

    parsed = tomllib.loads(first_render)
    assert parsed["active_model"] == "local"
    assert [server["name"] for server in parsed["mcp_servers"]] == [
        "existing",
        "workflow",
    ]
    workflow_server = parsed["mcp_servers"][1]
    assert workflow_server == {
        "name": "workflow",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "workflow_memory.server"],
        "env": {"WORKFLOW_DB": str(workflow_database_path(workdir))},
    }
    assert first_render.count('name = "workflow"') == 1


@pytest.mark.parametrize("existing_kind", ["incompatible", "duplicate"])
def test_configure_workdir_rejects_incompatible_workflow_name_without_rewriting(
    existing_kind: str, tmp_path: Path
) -> None:
    workdir = tmp_path / existing_kind
    config_path = workdir / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    if existing_kind == "incompatible":
        existing = (
            "[[mcp_servers]]\n"
            'name = "workflow"\n'
            'transport = "http"\n'
            'url = "http://127.0.0.1:9000"\n'
        )
    else:
        exact_server = render_mcp_config(workflow_database_path(workdir))
        existing = f"{exact_server}\n{exact_server}"
    config_path.write_text(existing, encoding="utf-8")

    with pytest.raises(
        WorkflowConfigurationError, match="incompatible MCP server named 'workflow'"
    ):
        configure_workdir(workdir)

    assert config_path.read_text(encoding="utf-8") == existing
