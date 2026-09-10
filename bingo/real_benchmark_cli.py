"""Command line entry point for reproducible real-repository benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_project_env
from .metrics import _make_provider_client, _provider_profile
from .models import FakeModelClient
from .real_benchmark import (
    RealRepositoryEvaluator,
    RetrievalAblationEvaluator,
    inventory_repository,
    load_real_benchmark,
    render_real_benchmark_report,
)

DEFAULT_MANIFEST = "benchmarks/real-repositories.json"
DEFAULT_E2E_ARTIFACT = "artifacts/real-repository-e2e.json"
DEFAULT_RETRIEVAL_ARTIFACT = "artifacts/real-repository-retrieval.json"
DEFAULT_REPORT = "docs/metrics/real-repository-validation.md"


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _provider_factory(provider):
    if provider == "scripted":
        def factory(task, workspace):
            del workspace
            outputs = task.get("scripted_outputs", [])
            if not outputs:
                raise ValueError(f"task {task['id']} has no scripted_outputs")
            return FakeModelClient(outputs)

        return factory, "scripted-smoke", "deterministic-non-LLM"
    profile = _provider_profile(provider)
    if profile["status"] != "ready":
        raise RuntimeError(profile["reason"])

    def factory(task, workspace):
        del task, workspace
        return _make_provider_client(provider)

    return factory, profile["provider"], profile["model"]


def _load_optional(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("inventory", "e2e", "retrieval", "report", "all"),
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--provider", choices=("scripted", "gpt", "claude", "deepseek"), default="scripted")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--latency-repetitions", type=int, default=5)
    parser.add_argument("--workspace-root", default=".bingo/benchmark-workspaces")
    parser.add_argument("--e2e-output", default=DEFAULT_E2E_ARTIFACT)
    parser.add_argument("--retrieval-output", default=DEFAULT_RETRIEVAL_ARTIFACT)
    parser.add_argument("--report-output", default=DEFAULT_REPORT)
    parser.add_argument("--inventory-output", default="artifacts/real-repository-inventory.json")
    parser.add_argument("--allow-vector-fallback", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_project_env(Path.cwd())
    manifest = load_real_benchmark(args.manifest)
    inventory_artifact = {
        "artifact_type": "real-repository-inventory-v1",
        "manifest_path": manifest["manifest_path"],
        "repositories": [inventory_repository(item) for item in manifest["repositories"]],
    }
    inventory_artifact["summary"] = {
        "repository_count": len(inventory_artifact["repositories"]),
        "source_files": sum(item["source_files"] for item in inventory_artifact["repositories"]),
        "physical_loc": sum(item["physical_loc"] for item in inventory_artifact["repositories"]),
    }
    _write_json(args.inventory_output, inventory_artifact)
    e2e = None
    retrieval = None
    if args.command in {"e2e", "all"}:
        factory, model_name, model_version = _provider_factory(args.provider)
        e2e = RealRepositoryEvaluator(
            args.manifest,
            artifact_path=args.e2e_output,
            workspace_root=Path(args.workspace_root) / "e2e" / args.provider,
            model_client_factory=factory,
            model_name=model_name,
            model_version=model_version,
            repetitions=args.repetitions,
        ).run()
    if args.command in {"retrieval", "all"}:
        retrieval = RetrievalAblationEvaluator(
            args.manifest,
            artifact_path=args.retrieval_output,
            workspace_root=Path(args.workspace_root) / "retrieval",
            latency_repetitions=args.latency_repetitions,
            require_vector=not args.allow_vector_fallback,
        ).run()
    if args.command == "report":
        e2e = _load_optional(args.e2e_output)
        retrieval = _load_optional(args.retrieval_output)
    if args.command in {"e2e", "retrieval", "report", "all"}:
        if e2e is None:
            e2e = _load_optional(args.e2e_output)
        if retrieval is None:
            retrieval = _load_optional(args.retrieval_output)
        render_real_benchmark_report(e2e, retrieval, args.report_output)
    print(
        json.dumps(
            {
                "command": args.command,
                "inventory": args.inventory_output,
                "e2e": args.e2e_output if e2e else "not-run",
                "retrieval": args.retrieval_output if retrieval else "not-run",
                "report": args.report_output if args.command != "inventory" else "not-run",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
