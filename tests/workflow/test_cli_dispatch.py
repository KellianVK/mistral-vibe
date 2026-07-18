from __future__ import annotations

from pathlib import Path

import pytest

from vibe.cli import entrypoint as cli_entrypoint
from vibe.workflow import entrypoint as workflow_entrypoint


def test_vibe_workflow_dispatches_before_flat_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "vibe.workflow.entrypoint.main", lambda argv: calls.append(list(argv)) or 0
    )
    monkeypatch.setattr(
        "sys.argv", ["vibe", "workflow", "status", "--workdir", str(tmp_path)]
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_entrypoint.main()

    assert excinfo.value.code == 0
    assert calls == [["status", "--workdir", str(tmp_path)]]


def test_workflow_status_without_database_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = workflow_entrypoint.main(["status", "--workdir", str(tmp_path)])

    assert exit_code == 1
    assert "No workflow database" in capsys.readouterr().err


def test_run_parser_defaults() -> None:
    parser = workflow_entrypoint.build_parser()
    args = parser.parse_args(["run", "--goal", "Todo API"])

    assert args.goal == "Todo API"
    assert args.roles is None
    assert args.no_warm_start is False
    assert args.no_board is False
    assert args.port == workflow_entrypoint.DEFAULT_BOARD_PORT


def test_run_parser_requires_goal() -> None:
    parser = workflow_entrypoint.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])
