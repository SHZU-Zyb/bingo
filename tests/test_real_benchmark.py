import json
from pathlib import Path

import pytest

from bingo.models import FakeModelClient
from bingo.real_benchmark import (
    RealRepositoryEvaluator,
    RetrievalAblationEvaluator,
    inventory_repository,
    load_real_benchmark,
    render_real_benchmark_report,
)
from bingo.real_benchmark_cli import main as benchmark_cli_main


def write_manifest(tmp_path, repo_path, *, tasks=None, queries=None, expected_revision=""):
    manifest = {
        "schema_version": 1,
        "repositories": [
            {
                "id": "sample",
                "path": str(repo_path),
                "expected_revision": expected_revision,
                "queries": queries or [],
            }
        ],
        "tasks": tasks or [],
    }
    path = tmp_path / "real-benchmark.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def test_manifest_inventory_counts_source_files_loc_and_exclusions(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "vendor").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "src" / "view.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    (repo / "vendor" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    manifest_path = write_manifest(tmp_path, repo)

    manifest = load_real_benchmark(manifest_path)
    inventory = inventory_repository(manifest["repositories"][0])

    assert manifest["repositories"][0]["path"] == str(repo.resolve())
    assert inventory["source_files"] == 2
    assert inventory["physical_loc"] == 3
    assert inventory["languages"] == {"Python": {"files": 1, "physical_loc": 2}, "TypeScript": {"files": 1, "physical_loc": 1}}
    assert inventory["revision"].startswith("snapshot:sha256:")
    assert len(inventory["snapshot_sha256"]) == 64


def test_manifest_rejects_duplicate_repo_and_revision_mismatch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    path = write_manifest(tmp_path, repo, expected_revision="deadbeef")

    manifest = load_real_benchmark(path)
    with pytest.raises(ValueError, match="revision mismatch"):
        inventory_repository(manifest["repositories"][0])

    data = json.loads(path.read_text(encoding="utf-8"))
    data["repositories"].append(dict(data["repositories"][0]))
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate repository"):
        load_real_benchmark(path)


def test_real_evaluator_runs_isolated_mutation_task_and_retains_logs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    tasks = [
        {
            "id": "repair-value",
            "repo_id": "sample",
            "category": "mutation-repair",
            "prompt": "Repair value() so it returns one.",
            "allowed_tools": ["read_file", "patch_file"],
            "step_budget": 3,
            "timeout_seconds": 20,
            "setup_replacements": [
                {"path": "module.py", "old_text": "return 1", "new_text": "return 0"}
            ],
            "expected_paths": ["module.py"],
            "verifier": "{python} -c \"from module import value; assert value() == 1\"",
        }
    ]
    manifest_path = write_manifest(tmp_path, repo, tasks=tasks)
    artifact_path = tmp_path / "artifacts" / "real-e2e.json"

    def model_factory(task, workspace):
        del task, workspace
        return FakeModelClient(
            [
                '<tool name="patch_file" path="module.py"><old_text>return 0</old_text><new_text>return 1</new_text></tool>',
                "<final>fixed</final>",
            ]
        )

    result = RealRepositoryEvaluator(
        manifest_path,
        artifact_path=artifact_path,
        workspace_root=tmp_path / "runs",
        model_client_factory=model_factory,
        model_name="fake",
        model_version="deterministic",
    ).run()

    assert artifact_path.is_file()
    assert result["summary"]["repository_count"] == 1
    assert result["summary"]["task_count"] == 1
    assert result["summary"]["completion_rate"] == 1.0
    assert result["summary"]["avg_llm_calls"] == 2.0
    assert result["summary"]["avg_parent_llm_calls"] == 2.0
    assert result["summary"]["avg_child_llm_calls"] == 0.0
    assert result["summary"]["avg_task_seconds"] >= 0
    assert result["summary"]["by_repository"]["sample"]["completion_rate"] == 1.0
    assert len(result["summary"]["completion_rate_ci95"]) == 2
    row = result["rows"][0]
    assert row["verifier_passed"] is True
    assert row["status"] == "pass"
    assert row["llm_calls"] == 2
    assert Path(row["verifier_stdout_artifact"]).is_file()
    assert Path(row["verifier_stderr_artifact"]).is_file()
    assert "return 1" in (repo / "module.py").read_text(encoding="utf-8")


class FakeRetrievalEngine:
    def __init__(self, root):
        self.root = root
        self._query_vectors = {}

    def index(self, complete=True):
        assert complete is True
        return {"files": 2, "chunks": 2, "vectors": 4, "vector_coverage": 1.0, "fallback_reason": ""}

    def search(self, query, mode, top_k=5):
        del top_k
        mapping = {
            ("vector", "find alpha"): ["noise.py", "alpha.py"],
            ("vector", "find beta"): ["noise.py"],
            ("vector", "no answer"): [],
            ("hybrid", "find alpha"): ["alpha.py"],
            ("hybrid", "find beta"): ["beta.py"],
            ("hybrid", "no answer"): [],
            ("auto", "find alpha"): ["alpha.py"],
            ("auto", "find beta"): ["beta.py"],
            ("auto", "no answer"): [],
        }
        paths = mapping[(mode, query)]
        duration = {"vector": 20.0, "hybrid": 10.0, "auto": 8.0}[mode]
        return {
            "hits": [{"sources": [{"path": path}]} for path in paths],
            "duration_ms": duration,
            "strategy": mode,
            "fallback_reason": "",
            "channels_used": [mode],
        }

    def close(self):
        return None


def test_retrieval_ablation_aggregates_real_repo_metrics_and_improvements(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "benchmarks").mkdir()
    (repo / "benchmarks" / "queries.json").write_text(
        '"find alpha"\n', encoding="utf-8"
    )
    (repo / "alpha.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (repo / "beta.py").write_text("def beta():\n    pass\n", encoding="utf-8")
    queries = [
        {"id": "alpha", "query": "find alpha", "kind": "semantic", "paths": ["alpha.py"]},
        {"id": "beta", "query": "find beta", "kind": "semantic", "paths": ["beta.py"]},
        {"id": "negative", "query": "no answer", "kind": "negative", "paths": []},
    ]
    manifest_path = write_manifest(tmp_path, repo, queries=queries)
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["repositories"][0]["exclude"] = ["benchmarks"]
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    artifact_path = tmp_path / "artifacts" / "real-retrieval.json"

    def engine_factory(root, repository):
        assert repository["exclude"] == ["benchmarks"]
        assert not (root / "benchmarks").exists()
        return FakeRetrievalEngine(root)

    result = RetrievalAblationEvaluator(
        manifest_path,
        artifact_path=artifact_path,
        workspace_root=tmp_path / "retrieval-runs",
        modes=("vector", "hybrid", "auto"),
        latency_repetitions=2,
        engine_factory=engine_factory,
    ).run()

    assert artifact_path.is_file()
    assert result["summary"]["repository_count"] == 1
    assert result["summary"]["query_count"] == 3
    assert result["modes"]["vector"]["recall_at_5"] == 0.5
    assert result["modes"]["vector"]["mrr"] == 0.25
    assert result["modes"]["hybrid"]["recall_at_5"] == 1.0
    assert result["modes"]["hybrid"]["mrr"] == 1.0
    assert result["modes"]["hybrid"]["no_answer_accuracy"] == 1.0
    assert result["modes"]["hybrid"]["per_repository"]["sample"]["recall_at_5"] == 1.0
    comparison = result["comparisons"]["hybrid_vs_vector"]
    assert comparison["recall_at_5_delta_points"] == 50.0
    assert comparison["mrr_relative_improvement_percent"] == 300.0
    assert comparison["p95_reduction_percent"] == 50.0


def test_report_renderer_keeps_protocol_and_evidence_limits_visible(tmp_path):
    e2e = {
        "artifact_path": "artifacts/e2e.json",
        "repositories": [{"id": "repo-a", "revision": "commit-a"}],
        "summary": {
            "repository_count": 2,
            "source_files": 3200,
            "physical_loc": 450000,
            "task_count": 20,
            "completed": 15,
            "completion_rate": 0.75,
            "avg_llm_calls": 4.5,
            "avg_task_seconds": 32.1,
            "p95_task_seconds": 81.4,
        },
        "reproducibility": {"model_name": "model-x", "repetitions": 3},
    }
    retrieval = {
        "artifact_path": "artifacts/retrieval.json",
        "repositories": [{"id": "repo-a", "revision": "commit-b"}],
        "summary": {"repository_count": 2, "query_count": 100},
        "modes": {
            "vector": {"recall_at_5": 0.72, "mrr": 0.61, "p95_ms": 80.0},
            "hybrid": {"recall_at_5": 0.84, "mrr": 0.74, "p95_ms": 50.0},
        },
        "comparisons": {
            "hybrid_vs_vector": {
                "recall_at_5_delta_points": 12.0,
                "mrr_relative_improvement_percent": 21.31,
                "p95_reduction_percent": 37.5,
            }
        },
        "protocol": {"latency_repetitions": 5, "warmup_per_query": 1},
    }
    output = tmp_path / "report.md"

    text = render_real_benchmark_report(e2e, retrieval, output)

    assert output.read_text(encoding="utf-8") == text
    assert "2 个真实仓库" in text
    assert "450.0 K LoC" in text
    assert "任务完成率 **75.0%**" in text
    assert "Recall@5 变化 **+12.00 个百分点**" in text
    assert "P95 降低 **37.50%**" in text
    assert "固定 commit" in text
    assert "原始 JSON" in text
    assert "仓库 revision 不一致" in text


def test_cli_inventory_writes_machine_readable_artifact(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    manifest_path = write_manifest(tmp_path, repo)
    output = tmp_path / "inventory.json"

    benchmark_cli_main(
        [
            "inventory",
            "--manifest",
            str(manifest_path),
            "--inventory-output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "repository_count": 1,
        "source_files": 1,
        "physical_loc": 1,
    }
    assert '"command": "inventory"' in capsys.readouterr().out
