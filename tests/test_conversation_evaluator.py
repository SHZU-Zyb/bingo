from copy import deepcopy
from pathlib import Path

import pytest

from bingo.conversation_evaluator import (
    MultiTurnBenchmarkEvaluator,
    load_multi_turn_benchmark,
    summarize_conversation_rows,
    validate_multi_turn_benchmark,
)


def test_summarize_conversation_rows_calculates_both_rates():
    summary = summarize_conversation_rows(
        [
            {"passed": True, "context_hits": 2, "context_targets": 2},
            {"passed": False, "context_hits": 0, "context_targets": 1},
        ]
    )

    assert summary["total_conversations"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["conversation_completion_rate"] == 0.5
    assert summary["context_hit_rate"] == pytest.approx(2 / 3)


def test_summarize_conversation_rows_handles_empty_denominators():
    summary = summarize_conversation_rows([])

    assert summary["conversation_completion_rate"] == 0.0
    assert summary["context_hit_rate"] == 0.0


def test_load_multi_turn_benchmark_validates_checked_in_schema():
    benchmark = load_multi_turn_benchmark(Path("benchmarks/multi_turn_tasks.json"))

    assert benchmark["schema_version"] == 1
    assert len(benchmark["tasks"]) == 1
    task = benchmark["tasks"][0]
    assert task["id"] == "remember_sample_sequence"
    assert len(task["turns"]) == 2
    assert task["turns"][1]["required_context"] == [
        {"id": "sample_sequence", "text": "1: alpha | 2: beta | 3: gamma"}
    ]


def _valid_task():
    return {
        "id": "valid",
        "fixture_repo": "tests/fixtures/bench_repo_patch",
        "allowed_tools": ["read_file"],
        "total_step_budget": 3,
        "turns": [{"id": "turn-1", "user": "Read sample.txt."}],
        "final_verifier": "python3 -c \"print('ok')\"",
        "scripted_outputs": ["<final>Done.</final>"],
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda task: task.update(turns=[]), "turns"),
        (lambda task: task.update(total_step_budget=0), "total_step_budget"),
        (lambda task: task.update(allowed_tools=[]), "allowed_tools"),
        (
            lambda task: task["turns"][0].update(required_context=[{"id": "missing-text"}]),
            "required_context",
        ),
    ],
)
def test_validate_multi_turn_benchmark_rejects_invalid_task_contract(mutate, message):
    task = _valid_task()
    mutate(task)

    with pytest.raises(ValueError, match=message):
        validate_multi_turn_benchmark(
            {"schema_version": 1, "tasks": [task]},
            repo_root=Path.cwd(),
        )


def test_multi_turn_benchmark_calculates_completion_rate(tmp_path):
    artifact_path = tmp_path / "multi-turn-benchmark.json"
    evaluator = MultiTurnBenchmarkEvaluator(
        benchmark_path=Path("benchmarks/multi_turn_tasks.json"),
        artifact_path=artifact_path,
        workspace_root=tmp_path / "workspaces",
    )

    artifact = evaluator.run()

    assert artifact_path.exists()
    assert artifact["summary"]["conversation_completion_rate"] == 1.0
    row = artifact["rows"][0]
    assert row["passed"] is True
    assert row["verifier_passed"] is True
    assert len(row["turns"]) == 2
    assert row["turns"][0]["run_id"] != row["turns"][1]["run_id"]
    assert {turn["session_id"] for turn in row["turns"]} == {row["session_id"]}


def test_context_hit_rate_uses_rendered_relevant_memory(tmp_path):
    evaluator = MultiTurnBenchmarkEvaluator(
        benchmark_path=Path("benchmarks/multi_turn_tasks.json"),
        artifact_path=tmp_path / "multi-turn-benchmark.json",
        workspace_root=tmp_path / "workspaces",
    )

    artifact = evaluator.run()

    assert artifact["summary"]["context_hits"] == 1
    assert artifact["summary"]["context_targets"] == 1
    assert artifact["summary"]["context_hit_rate"] == 1.0
    dependent_turn = artifact["rows"][0]["turns"][1]
    assert dependent_turn["context_hits"] == 1
    assert dependent_turn["context_target_results"] == [
        {"id": "sample_sequence", "text": "1: alpha | 2: beta | 3: gamma", "hit": True}
    ]


def test_context_hit_rate_does_not_count_missing_target(tmp_path):
    evaluator = MultiTurnBenchmarkEvaluator(
        benchmark_path=Path("benchmarks/multi_turn_tasks.json"),
        artifact_path=tmp_path / "multi-turn-benchmark.json",
        workspace_root=tmp_path / "workspaces",
    )
    task = evaluator.load()["tasks"][0]
    task["turns"][1]["required_context"] = [
        {"id": "missing", "text": "this target was never remembered"}
    ]

    row = evaluator.run_task(task)
    summary = summarize_conversation_rows([row])

    assert row["context_hits"] == 0
    assert row["context_targets"] == 1
    assert summary["context_hit_rate"] == 0.0


def test_failed_final_verifier_prevents_conversation_completion(tmp_path):
    evaluator = MultiTurnBenchmarkEvaluator(
        benchmark_path=Path("benchmarks/multi_turn_tasks.json"),
        artifact_path=tmp_path / "multi-turn-benchmark.json",
        workspace_root=tmp_path / "workspaces",
    )
    task = deepcopy(evaluator.load()["tasks"][0])
    task["final_verifier"] = "python3 -c \"raise SystemExit(1)\""

    row = evaluator.run_task(task)

    assert all(turn["passed"] for turn in row["turns"])
    assert row["verifier_passed"] is False
    assert row["passed"] is False


def test_tool_allowlist_blocks_undeclared_tool_and_records_trace(tmp_path):
    evaluator = MultiTurnBenchmarkEvaluator(
        benchmark_path=Path("benchmarks/multi_turn_tasks.json"),
        artifact_path=tmp_path / "multi-turn-benchmark.json",
        workspace_root=tmp_path / "workspaces",
    )
    task = deepcopy(evaluator.load()["tasks"][0])
    task["turns"] = [{"id": "blocked-write", "user": "Try to patch sample.txt."}]
    task["scripted_outputs"] = [
        '<tool name="patch_file" path="sample.txt"><old_text>beta</old_text><new_text>changed</new_text></tool>',
        "<final>The undeclared tool was blocked.</final>",
    ]

    row = evaluator.run_task(task)

    turn = row["turns"][0]
    assert turn["tool_names"] == ["patch_file"]
    assert turn["tool_error_codes"] == ["unknown_tool"]
    sample_text = (Path(row["fixture_copy_root"]) / "sample.txt").read_text(encoding="utf-8")
    assert "changed" not in sample_text
    assert "beta" in sample_text
