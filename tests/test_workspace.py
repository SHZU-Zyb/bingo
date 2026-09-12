import subprocess

from bingo import workspace as workspace_module
from bingo.workspace import WorkspaceContext


def test_workspace_git_decodes_process_output_as_utf8(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(workspace_module.subprocess, "run", fake_run)

    WorkspaceContext.build(tmp_path)

    assert calls
    assert all(kwargs.get("encoding") == "utf-8" for _, kwargs in calls)
    assert all(kwargs.get("errors") == "replace" for _, kwargs in calls)
