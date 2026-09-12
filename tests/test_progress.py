from io import StringIO
import json

from bingo.models import FakeModelClient
from bingo.progress import ConsoleProgressRenderer
from bingo.runtime import Bingo, SessionStore
from bingo.workspace import WorkspaceContext


def _build_agent(tmp_path, outputs, event_sink, secret_env_names=()):
    (tmp_path / "README.md").write_text("progress demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    return Bingo(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=SessionStore(tmp_path / ".bingo" / "sessions"),
        approval_policy="auto",
        event_sink=event_sink,
        secret_env_names=secret_env_names,
    )


def test_renderer_shows_attempt_tool_start_and_success():
    stream = StringIO()
    renderer = ConsoleProgressRenderer(stream=stream, max_steps=24)

    renderer({"event": "run_started"})
    renderer({"event": "model_requested", "attempts": 1, "tool_steps": 0})
    renderer(
        {
            "event": "tool_started",
            "name": "read_file",
            "args": {"path": "tests/test_demo.py", "start": 1, "end": 80},
        }
    )
    renderer(
        {
            "event": "tool_executed",
            "name": "read_file",
            "tool_status": "ok",
            "duration_ms": 15,
            "result": "# tests/test_demo.py\n   1: def test_demo(): pass",
        }
    )

    output = stream.getvalue()
    assert "[run] started" in output
    assert "[1/24] thinking..." in output
    assert "[1/24] tool read_file path=tests/test_demo.py lines=1-80" in output
    assert "[1/24] ok 15ms" in output
    assert "def test_demo" not in output


def test_renderer_shows_concise_shell_failure():
    stream = StringIO()
    renderer = ConsoleProgressRenderer(stream=stream, max_steps=12)
    renderer({"event": "model_requested", "attempts": 2, "tool_steps": 1})
    renderer(
        {
            "event": "tool_started",
            "name": "run_shell",
            "args": {"command": "python -m pytest tests/test_demo.py -v " + ("x" * 200)},
        }
    )
    renderer(
        {
            "event": "tool_executed",
            "name": "run_shell",
            "tool_status": "error",
            "tool_error_code": "tool_failed",
            "duration_ms": 830,
            "result": "exit_code: 1\nstdout:\nFAILED test_demo\nstderr:\nAssertionError\n" + ("tail " * 100),
        }
    )

    lines = stream.getvalue().splitlines()
    assert lines[0].startswith("[2/12] thinking")
    assert lines[1].startswith("[2/12] tool run_shell command=")
    assert len(lines[1]) < 160
    assert "error tool_failed" in lines[2]
    assert "exit_code: 1" in lines[2]
    assert len(lines[2]) < 220


def test_renderer_shows_when_a_read_skips_cached_lines():
    stream = StringIO()
    renderer = ConsoleProgressRenderer(stream=stream, max_steps=12)
    renderer({"event": "model_requested", "attempts": 2, "tool_steps": 1})
    renderer(
        {
            "event": "tool_executed",
            "name": "read_file",
            "tool_status": "ok",
            "read_cache_action": "trimmed",
            "skipped_cached_range": "1-78",
            "effective_args": {"path": "bingo/embeddings.py", "start": 79, "end": 120},
        }
    )

    assert stream.getvalue().splitlines()[-1] == "[2/12] ok cached=1-78 read=79-120"


def test_renderer_only_shows_recovery_checkpoints():
    stream = StringIO()
    renderer = ConsoleProgressRenderer(stream=stream)

    renderer({"event": "checkpoint_created", "trigger": "tool_executed"})
    renderer({"event": "checkpoint_created", "trigger": "context_reduction"})
    renderer({"event": "runtime_identity_mismatch", "fields": ["workspace_fingerprint"]})

    output = stream.getvalue()
    assert "tool_executed" not in output
    assert "recovery context_reduction" in output
    assert "workspace_fingerprint" in output


def test_renderer_shows_final_stop_reason_and_duration():
    stream = StringIO()
    renderer = ConsoleProgressRenderer(stream=stream)

    renderer(
        {
            "event": "run_finished",
            "status": "stopped",
            "stop_reason": "step_limit_reached",
            "run_duration_ms": 3250,
        }
    )

    assert stream.getvalue().strip() == "[run] stopped step_limit_reached 3.25s"


def test_runtime_emits_tool_started_before_tool_executed(tmp_path):
    events = []
    agent = _build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":5}}</tool>',
            "<final>Done.</final>",
        ],
        events.append,
    )

    assert agent.ask("Read README.md") == "Done."

    event_names = [event["event"] for event in events]
    assert event_names.index("tool_started") < event_names.index("tool_executed")
    started = next(event for event in events if event["event"] == "tool_started")
    assert started["name"] == "read_file"
    assert started["args"]["path"] == "README.md"

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "tool_started" for event in trace_events)


def test_runtime_sink_receives_redacted_event(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGO_PROGRESS_SECRET", "test-key-test-secret-123")
    events = []
    agent = _build_agent(
        tmp_path,
        ["<final>Done.</final>"],
        events.append,
        secret_env_names=("BINGO_PROGRESS_SECRET",),
    )
    agent.ask("Finish")

    agent.emit_trace(
        agent.current_task_state,
        "diagnostic",
        {"result": "token test-key-test-secret-123 must not leak"},
    )

    assert "test-key-test-secret-123" not in events[-1]["result"]
    assert "<redacted>" in events[-1]["result"]


def test_runtime_ignores_sink_failure_and_keeps_trace(tmp_path):
    def failing_sink(_event):
        raise OSError("terminal closed")

    agent = _build_agent(tmp_path, ["<final>Still done.</final>"], failing_sink)

    assert agent.ask("Finish despite renderer failure") == "Still done."
    trace_text = agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8")
    assert '"event": "run_finished"' in trace_text
