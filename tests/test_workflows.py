import json
import threading
from types import SimpleNamespace

import pytest

from bingo import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from bingo.models import clone_model_client
from bingo.verification_parser import needs_diagnostic_agent, parse_verification_output
from bingo.workflow_engine import WorkflowEngine
from bingo.workflow_store import WorkflowStore


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".bingo" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


def test_model_client_clone_has_isolated_metadata_and_shared_fake_script():
    parent = FakeModelClient(["<final>one</final>", "<final>two</final>"])
    child_one = clone_model_client(parent)
    child_two = clone_model_client(parent)

    assert child_one is not child_two
    assert child_one.complete("first", 100) == "<final>one</final>"
    assert child_two.complete("second", 100) == "<final>two</final>"
    child_one.last_completion_metadata["child"] = 1
    assert "child" not in child_two.last_completion_metadata
    assert parent.prompts == ["first", "second"]


def test_parser_extracts_pytest_counts_and_failure_cards():
    output = (
        "tests/test_router.py:41: AssertionError\n"
        "FAILED tests/test_router.py::test_semantic_route - AssertionError: expected hybrid\n"
        "FAILED tests/test_cache.py::test_stale_cache - ValueError: stale hash\n"
        "2 failed, 18 passed, 1 skipped in 1.24s\n"
    )

    result = parse_verification_output("python -m pytest -q", output, "", 1)

    assert result["status"] == "failed"
    assert result["counts"] == {"passed": 18, "failed": 2, "skipped": 1, "errors": 0}
    assert [item["test_id"] for item in result["failures"]] == [
        "tests/test_router.py::test_semantic_route",
        "tests/test_cache.py::test_stale_cache",
    ]
    assert result["failures"][0]["exception"] == "AssertionError"


def test_clear_single_failure_does_not_need_diagnostic_agent():
    result = parse_verification_output(
        "python -m pytest -q",
        "FAILED tests/test_router.py::test_route - AssertionError: expected hybrid, got keyword\n1 failed, 8 passed\n",
        "",
        1,
    )

    decision = needs_diagnostic_agent(result)

    assert decision == {"required": False, "reasons": []}


def test_multiple_failures_require_diagnostic_reasoning():
    result = parse_verification_output(
        "python -m pytest -q",
        "FAILED tests/test_a.py::test_a - AssertionError: one\n"
        "FAILED tests/test_b.py::test_b - AssertionError: two\n"
        "2 failed\n",
        "",
        1,
    )

    decision = needs_diagnostic_agent(result)

    assert decision["required"] is True
    assert "multiple_failures" in decision["reasons"]


def test_parser_marks_unstructured_failure_for_diagnosis():
    result = parse_verification_output("make check", "build stopped unexpectedly\n", "", 2)

    decision = needs_diagnostic_agent(result)

    assert result["status"] == "failed"
    assert result["failures"] == []
    assert "unstructured_failure" in decision["reasons"]


def test_workflow_store_keeps_full_artifact_and_returns_bounded_lines(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    workflow_id = store.create("verify_and_diagnose", "run tests")
    content = "\n".join(f"line-{index}" for index in range(1, 301))

    reference = store.write_text(workflow_id, "runner", "stdout.log", content)
    excerpt = store.read_text(workflow_id, "runner", "stdout.log", start_line=198, end_line=202)

    assert reference["chars"] == len(content)
    assert len(reference["content_hash"]) == 64
    assert excerpt["content"].splitlines() == [
        " 198: line-198",
        " 199: line-199",
        " 200: line-200",
        " 201: line-201",
        " 202: line-202",
    ]
    with pytest.raises(ValueError, match="invalid artifact"):
        store.read_text(workflow_id, "runner", "../secret.txt")


def test_parallel_workflow_rejects_single_branch(tmp_path):
    engine = WorkflowEngine(tmp_path, WorkflowStore(tmp_path / "workflows"), parallel_runner=lambda packet: {})

    with pytest.raises(ValueError, match="at least two"):
        engine.run_parallel("inspect", [{"id": "api", "task": "inspect api", "scope": ["api"]}])


def test_parallel_workflow_rejects_overlapping_scopes_and_serial_worker_limit(tmp_path):
    engine = WorkflowEngine(tmp_path, WorkflowStore(tmp_path / "workflows"), parallel_runner=lambda packet: {})
    branches = [
        {"id": "api", "task": "inspect api", "scope": ["bingo/api"]},
        {"id": "auth", "task": "inspect auth", "scope": ["bingo/api/auth"]},
    ]

    with pytest.raises(ValueError, match="overlap"):
        engine.run_parallel("inspect", branches)
    with pytest.raises(ValueError, match="at least two workers"):
        engine.run_parallel(
            "inspect",
            [
                {"id": "api", "task": "inspect api", "scope": ["api"]},
                {"id": "storage", "task": "inspect storage", "scope": ["storage"]},
            ],
            max_parallel=1,
        )


def test_parallel_workflow_runs_independent_branches_concurrently(tmp_path):
    barrier = threading.Barrier(2)

    def runner(packet):
        barrier.wait(timeout=2)
        return {
            "summary": f"checked {packet['node_id']}",
            "findings": [{"message": f"finding-{packet['node_id']}"}],
            "evidence_refs": [f"{packet['scope'][0]}/module.py:1-5"],
        }

    engine = WorkflowEngine(tmp_path, WorkflowStore(tmp_path / "workflows"), parallel_runner=runner)
    result = engine.run_parallel(
        "inspect modules",
        [
            {"id": "api", "task": "inspect api", "scope": ["api"]},
            {"id": "storage", "task": "inspect storage", "scope": ["storage"]},
        ],
    )

    assert result["status"] == "completed"
    assert {item["node_id"] for item in result["reports"]} == {"api", "storage"}
    assert all("artifact_ref" in item for item in result["reports"])


def test_verification_workflow_keeps_clear_failure_local(tmp_path):
    diagnosis_calls = []

    def command_runner(**kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="FAILED tests/test_router.py::test_route - AssertionError: expected hybrid\n1 failed, 8 passed\n",
            stderr="",
        )

    engine = WorkflowEngine(
        tmp_path,
        WorkflowStore(tmp_path / "workflows"),
        diagnosis_runner=lambda packet: diagnosis_calls.append(packet),
        command_runner=command_runner,
    )

    result = engine.run_verification("check route", "python -m pytest -q")

    assert result["diagnosis"]["invoked"] is False
    assert result["verification"]["counts"]["failed"] == 1
    assert diagnosis_calls == []
    assert "expected hybrid" in json.dumps(result)


def test_verification_workflow_uses_child_only_for_ambiguous_failure(tmp_path):
    packets = []
    noisy_tail = "NOISY INTERNAL DETAIL\n" * 1000

    def command_runner(**kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=(
                "FAILED tests/test_a.py::test_a - AssertionError: one\n"
                "FAILED tests/test_b.py::test_b - AssertionError: two\n"
                "2 failed\n"
                + noisy_tail
            ),
            stderr="",
        )

    def diagnose(packet):
        packets.append(packet)
        return {
            "summary": "both failures share one cache invalidation cause",
            "root_cause": "cache key omits repository fingerprint",
            "confidence": 0.88,
            "evidence_refs": ["bingo/cache.py:20-40"],
        }

    store = WorkflowStore(tmp_path / "workflows")
    engine = WorkflowEngine(
        tmp_path,
        store,
        diagnosis_runner=diagnose,
        command_runner=command_runner,
    )

    result = engine.run_verification("check cache", "python -m pytest -q")

    assert result["diagnosis"]["invoked"] is True
    assert result["diagnosis"]["summary"] == "both failures share one cache invalidation cause"
    assert "NOISY INTERNAL DETAIL" not in json.dumps(result)
    assert "stdout" not in packets[0]
    stdout_ref = result["artifacts"]["stdout"]
    assert (tmp_path / "workflows" / result["workflow_id"] / stdout_ref).read_text(encoding="utf-8").endswith(noisy_tail)


def test_verification_preserves_local_facts_when_diagnostic_child_fails(tmp_path):
    def command_runner(**kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=(
                "FAILED tests/test_a.py::test_a - AssertionError: one\n"
                "FAILED tests/test_b.py::test_b - AssertionError: two\n"
                "2 failed\n"
            ),
            stderr="",
        )

    def failed_diagnosis(packet):
        raise RuntimeError("model backend unavailable")

    store = WorkflowStore(tmp_path / "workflows")
    engine = WorkflowEngine(
        tmp_path,
        store,
        diagnosis_runner=failed_diagnosis,
        command_runner=command_runner,
    )

    result = engine.run_verification("check", "python -m pytest -q")

    assert result["verification"]["counts"]["failed"] == 2
    assert result["diagnosis"]["invoked"] is True
    assert result["diagnosis"]["status"] == "failed"
    assert "model backend unavailable" in result["diagnosis"]["summary"]
    assert (tmp_path / "workflows" / result["workflow_id"] / "diagnostician" / "error.log").is_file()


def test_runtime_registers_workflow_tools_and_keeps_raw_output_out_of_session(tmp_path):
    agent = build_agent(tmp_path, [])

    assert {"parallel_workflow", "verification_workflow", "read_workflow_artifact"} <= set(agent.tools)
    result = agent.run_tool(
        "verification_workflow",
        {
            "objective": "smoke check",
            "command": 'python -c "print(\'1 passed in 0.01s\')"',
            "timeout": 20,
        },
    )

    payload = json.loads(result)
    assert payload["verification"]["status"] == "passed"
    assert payload["diagnosis"]["invoked"] is False
    assert "stdout" not in json.dumps(agent.session["workflows"])


def test_runtime_parallel_children_are_read_only_isolated_and_artifact_backed(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<final>{"summary":"checked api","evidence_refs":["api.py:1"]}</final>',
            '<final>{"summary":"checked storage","evidence_refs":["storage.py:2"]}</final>',
        ],
    )

    result = agent.run_tool(
        "parallel_workflow",
        {
            "objective": "inspect independent modules",
            "branches": [
                {"id": "api", "task": "inspect API", "scope": ["api"]},
                {"id": "storage", "task": "inspect storage", "scope": ["storage"]},
            ],
            "max_parallel": 2,
            "max_steps": 2,
        },
    )

    payload = json.loads(result)
    assert payload["status"] == "completed"
    assert len(payload["reports"]) == 2
    child_prompts = agent.model_client.prompts
    assert len(child_prompts) == 2
    assert all("read-only explorer child Agent" in prompt for prompt in child_prompts)
    assert all("write_file(" not in prompt and "parallel_workflow(" not in prompt for prompt in child_prompts)

    workflow_id = payload["workflow_id"]
    first_ref = payload["reports"][0]["artifact_ref"]
    node_id, artifact = first_ref.split("/", 1)
    excerpt = agent.run_tool(
        "read_workflow_artifact",
        {
            "workflow_id": workflow_id,
            "node_id": node_id,
            "artifact": artifact,
            "start_line": 1,
            "end_line": 20,
        },
    )
    assert workflow_id in excerpt
    assert "summary" in excerpt
