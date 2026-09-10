"""Reproducible real-repository Agent and retrieval benchmarks."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .models import FakeModelClient
from .run_store import RunStore
from .runtime import Bingo, SessionStore
from .task_state import STOP_REASON_FINAL_ANSWER_RETURNED
from .workspace import WorkspaceContext

REAL_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_COPY_IGNORES = {
    ".git",
    ".bingo",
    ".env",
    ".env.local",
    ".env.production",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
}


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _safe_relative_path(value, field="path"):
    text = str(value).strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid relative {field}: {value}")
    return path.as_posix()


def _git_output(root, *args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _iter_repository_files(root, extra_ignores=()):
    root = Path(root).resolve()
    ignored = DEFAULT_COPY_IGNORES | {str(item) for item in extra_ignores}
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        yield path, relative


def _snapshot_sha256(root, extra_ignores=()):
    sha = hashlib.sha256()
    for path, relative in _iter_repository_files(root, extra_ignores):
        sha.update(relative.as_posix().encode("utf-8"))
        sha.update(b"\0")
        sha.update(path.read_bytes())
        sha.update(b"\0")
    return sha.hexdigest()


def _normalize_query(item, repo_id):
    if not isinstance(item, dict):
        raise TypeError(f"repository {repo_id} query must be an object")
    query_id = str(item.get("id", "")).strip()
    query = str(item.get("query", "")).strip()
    paths = item.get("paths", [])
    if not query_id or not query or not isinstance(paths, list):
        raise ValueError(f"repository {repo_id} query requires id, query, and paths")
    return {
        "id": query_id,
        "query": query,
        "kind": str(item.get("kind", "semantic")).strip() or "semantic",
        "paths": [_safe_relative_path(path, "query path") for path in paths],
    }


def load_real_benchmark(path):
    """Load and normalize the versioned real-repository manifest."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("real benchmark manifest must be an object")
    if int(data.get("schema_version", 0)) != REAL_BENCHMARK_SCHEMA_VERSION:
        raise ValueError("unsupported real benchmark schema_version")
    repositories = data.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError("real benchmark requires at least one repository")
    normalized_repositories = []
    repo_ids = set()
    for raw in repositories:
        if not isinstance(raw, dict):
            raise TypeError("repository entry must be an object")
        repo_id = str(raw.get("id", "")).strip()
        if not repo_id:
            raise ValueError("repository id must not be empty")
        if repo_id in repo_ids:
            raise ValueError(f"duplicate repository id: {repo_id}")
        repo_ids.add(repo_id)
        raw_path = Path(str(raw.get("path", "")).strip())
        repo_path = raw_path if raw_path.is_absolute() else path.parent / raw_path
        repo_path = repo_path.resolve()
        if not repo_path.is_dir():
            raise ValueError(f"repository path does not exist: {repo_path}")
        queries = raw.get("queries", [])
        if raw.get("queries_file"):
            query_path = path.parent / str(raw["queries_file"])
            queries = json.loads(query_path.read_text(encoding="utf-8"))
        if not isinstance(queries, list):
            raise TypeError(f"repository {repo_id} queries must be a list")
        normalized_repositories.append(
            {
                **raw,
                "id": repo_id,
                "path": str(repo_path),
                "expected_revision": str(raw.get("expected_revision", "")).strip(),
                "exclude": [str(item) for item in raw.get("exclude", [])],
                "queries": [_normalize_query(item, repo_id) for item in queries],
            }
        )
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise TypeError("tasks must be a list")
    task_ids = set()
    normalized_tasks = []
    required = {
        "id",
        "repo_id",
        "category",
        "prompt",
        "allowed_tools",
        "step_budget",
        "verifier",
    }
    for raw in tasks:
        if not isinstance(raw, dict):
            raise TypeError("task entry must be an object")
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(f"real benchmark task is missing: {', '.join(missing)}")
        task_id = str(raw["id"]).strip()
        repo_id = str(raw["repo_id"]).strip()
        if not task_id or task_id in task_ids:
            raise ValueError(f"duplicate or empty task id: {task_id}")
        if repo_id not in repo_ids:
            raise ValueError(f"unknown task repository: {repo_id}")
        task_ids.add(task_id)
        replacements = []
        for replacement in raw.get("setup_replacements", []):
            replacements.append(
                {
                    "path": _safe_relative_path(replacement.get("path", ""), "setup path"),
                    "old_text": str(replacement.get("old_text", "")),
                    "new_text": str(replacement.get("new_text", "")),
                }
            )
            if not replacements[-1]["old_text"]:
                raise ValueError(f"task {task_id} setup old_text must not be empty")
        allowed_tools = [str(item).strip() for item in raw["allowed_tools"]]
        if not allowed_tools or any(not item for item in allowed_tools):
            raise ValueError(f"task {task_id} allowed_tools must not be empty")
        normalized_tasks.append(
            {
                **raw,
                "id": task_id,
                "repo_id": repo_id,
                "category": str(raw["category"]).strip(),
                "prompt": str(raw["prompt"]).strip(),
                "allowed_tools": allowed_tools,
                "step_budget": int(raw["step_budget"]),
                "timeout_seconds": int(raw.get("timeout_seconds", 300)),
                "verifier": str(raw["verifier"]).strip(),
                "expected_paths": [
                    _safe_relative_path(item, "expected path")
                    for item in raw.get("expected_paths", [])
                ],
                "setup_replacements": replacements,
            }
        )
    return {
        "schema_version": REAL_BENCHMARK_SCHEMA_VERSION,
        "manifest_path": str(path),
        "repositories": normalized_repositories,
        "tasks": normalized_tasks,
    }


def inventory_repository(repository):
    """Count physical source files/lines and verify a pinned revision when declared."""
    root = Path(repository["path"]).resolve()
    extra_ignores = repository.get("exclude", [])
    languages = {}
    source_files = 0
    physical_loc = 0
    total_files = 0
    total_bytes = 0
    for path, _ in _iter_repository_files(root, extra_ignores):
        total_files += 1
        total_bytes += path.stat().st_size
        language = LANGUAGE_SUFFIXES.get(path.suffix.lower())
        if not language:
            continue
        line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        source_files += 1
        physical_loc += line_count
        bucket = languages.setdefault(language, {"files": 0, "physical_loc": 0})
        bucket["files"] += 1
        bucket["physical_loc"] += line_count
    snapshot = _snapshot_sha256(root, extra_ignores)
    commit = _git_output(root, "rev-parse", "HEAD")
    revision = commit or f"snapshot:sha256:{snapshot}"
    expected = str(repository.get("expected_revision", "")).strip()
    if expected and not (revision == expected or commit.startswith(expected)):
        raise ValueError(
            f"repository {repository.get('id')} revision mismatch: expected {expected}, got {revision}"
        )
    dirty = bool(_git_output(root, "status", "--porcelain")) if commit else None
    return {
        "id": repository["id"],
        "path": str(root),
        "revision": revision,
        "commit": commit,
        "dirty": dirty,
        "snapshot_sha256": snapshot,
        "total_files": total_files,
        "source_files": source_files,
        "physical_loc": physical_loc,
        "total_bytes": total_bytes,
        "languages": dict(sorted(languages.items())),
    }


def _copy_repository(source, destination, extra_ignores=()):
    ignored = DEFAULT_COPY_IGNORES | {str(item) for item in extra_ignores}

    def ignore(_directory, names):
        return sorted(name for name in names if name in ignored)

    return shutil.copytree(source, destination, ignore=ignore)


def _apply_replacements(root, task):
    for replacement in task.get("setup_replacements", []):
        path = (Path(root) / replacement["path"]).resolve()
        try:
            path.relative_to(Path(root).resolve())
        except ValueError:
            raise ValueError("setup path escapes repository copy") from None
        if not path.is_file():
            raise ValueError(f"setup path is not a file: {replacement['path']}")
        text = path.read_text(encoding="utf-8")
        count = text.count(replacement["old_text"])
        if count != 1:
            raise ValueError(
                f"task {task['id']} setup replacement must match once, found {count}"
            )
        path.write_text(
            text.replace(replacement["old_text"], replacement["new_text"], 1),
            encoding="utf-8",
        )


def _percentile(values, quantile):
    values = sorted(float(item) for item in values)
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * float(quantile)) - 1)
    return values[index]


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _wilson_interval(successes, total, z=1.96):
    if total <= 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _completion_group(rows):
    rows = list(rows)
    completed = sum(1 for row in rows if row["passed"])
    return {
        "task_count": len(rows),
        "completed": completed,
        "completion_rate": completed / len(rows) if rows else 0.0,
        "avg_llm_calls": _mean(row["llm_calls"] for row in rows),
        "avg_task_seconds": _mean(row["task_seconds"] for row in rows),
    }


def _trace_metrics(root, parent_trace=None):
    total_calls = 0
    tool_calls = 0
    policy_rejections = 0
    security_events = Counter()
    input_tokens = 0
    output_tokens = 0
    token_samples = 0
    parent_calls = 0
    parent_trace = Path(parent_trace).resolve() if parent_trace else None
    for trace_path in Path(root).rglob("trace.jsonl"):
        is_parent = parent_trace is not None and trace_path.resolve() == parent_trace
        for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "model_requested":
                total_calls += 1
                if is_parent:
                    parent_calls += 1
            elif event.get("event") == "tool_started":
                tool_calls += 1
            elif event.get("event") == "tool_executed":
                if event.get("tool_status") == "rejected":
                    policy_rejections += 1
                if event.get("security_event_type"):
                    security_events[str(event["security_event_type"])] += 1
            elif event.get("event") == "model_parsed":
                metadata = event.get("completion_metadata", {}) or {}
                if metadata.get("input_tokens") is not None:
                    input_tokens += int(metadata["input_tokens"])
                    output_tokens += int(metadata.get("output_tokens") or 0)
                    token_samples += 1
    return {
        "llm_calls": total_calls,
        "parent_llm_calls": parent_calls,
        "child_llm_calls": max(0, total_calls - parent_calls),
        "tool_calls": tool_calls,
        "policy_rejections": policy_rejections,
        "security_events": dict(security_events),
        "input_tokens": input_tokens if token_samples else None,
        "output_tokens": output_tokens if token_samples else None,
        "token_usage_coverage": token_samples / total_calls if total_calls else 0.0,
    }


class RealRepositoryEvaluator:
    """Execute verifier-scored Agent tasks on isolated copies of real repositories."""

    def __init__(
        self,
        manifest_path,
        *,
        artifact_path,
        workspace_root,
        model_client_factory,
        model_name,
        model_version,
        repetitions=1,
        max_new_tokens=512,
    ):
        self.manifest_path = Path(manifest_path)
        self.artifact_path = Path(artifact_path).resolve()
        self.workspace_root = Path(workspace_root).resolve()
        self.model_client_factory = model_client_factory
        self.model_name = str(model_name)
        self.model_version = str(model_version)
        self.repetitions = int(repetitions)
        self.max_new_tokens = int(max_new_tokens)
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        if not callable(model_client_factory):
            raise TypeError("model_client_factory is required for end-to-end evaluation")

    def run(self):
        manifest = load_real_benchmark(self.manifest_path)
        repositories = {item["id"]: item for item in manifest["repositories"]}
        inventories = [inventory_repository(item) for item in manifest["repositories"]]
        rows = []
        for repetition in range(1, self.repetitions + 1):
            for task in manifest["tasks"]:
                rows.append(self._run_task(task, repositories[task["repo_id"]], repetition))
        summary = self._summarize(rows, inventories)
        artifact = {
            "schema_version": REAL_BENCHMARK_SCHEMA_VERSION,
            "artifact_type": "real-repository-e2e-v1",
            "artifact_path": str(self.artifact_path),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "manifest_path": str(Path(manifest["manifest_path"])),
            "manifest_sha256": hashlib.sha256(
                Path(manifest["manifest_path"]).read_bytes()
            ).hexdigest(),
            "reproducibility": {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "repetitions": self.repetitions,
                "max_new_tokens": self.max_new_tokens,
                "success_definition": (
                    "verifier exit 0 AND expected paths exist AND final answer returned "
                    "AND parent tool steps stay within budget"
                ),
            },
            "repositories": inventories,
            "summary": summary,
            "rows": rows,
        }
        _write_json(self.artifact_path, artifact)
        return artifact

    def _run_task(self, task, repository, repetition):
        task_started = time.monotonic()
        run_name = f"{task['id']}-r{repetition}"
        copy_root = self.workspace_root / run_name / repository["id"]
        if copy_root.exists():
            shutil.rmtree(copy_root)
        copy_root.parent.mkdir(parents=True, exist_ok=True)
        _copy_repository(repository["path"], copy_root)
        _apply_replacements(copy_root, task)
        workspace = WorkspaceContext.build(copy_root, repo_root_override=copy_root)
        model_client = self.model_client_factory(task=task, workspace=workspace)
        if isinstance(model_client, FakeModelClient) and self.model_name not in {
            "fake",
            "scripted-smoke",
        }:
            raise ValueError(
                "FakeModelClient results must be labelled fake or scripted-smoke"
            )
        agent = Bingo(
            model_client=model_client,
            workspace=workspace,
            session_store=SessionStore(copy_root / ".bingo" / "sessions"),
            run_store=RunStore(copy_root / ".bingo" / "runs"),
            approval_policy="auto",
            max_steps=task["step_budget"],
            max_new_tokens=self.max_new_tokens,
            allowed_tools=task["allowed_tools"],
        )
        agent_started = time.monotonic()
        agent_error = ""
        try:
            final_answer = agent.ask(task["prompt"])
        except Exception as exc:  # noqa: BLE001 - one task must not abort the benchmark suite
            final_answer = ""
            agent_error = f"{exc.__class__.__name__}: {exc}"
        agent_seconds = time.monotonic() - agent_started
        task_state = agent.current_task_state
        verifier_command = task["verifier"].replace("{python}", f'"{sys.executable}"')
        try:
            verifier = subprocess.run(
                verifier_command,
                cwd=copy_root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=task["timeout_seconds"],
            )
            verifier_exit_code = verifier.returncode
            verifier_stdout = verifier.stdout or ""
            verifier_stderr = verifier.stderr or ""
        except subprocess.TimeoutExpired as exc:
            verifier_exit_code = 124
            verifier_stdout = str(exc.stdout or "")
            verifier_stderr = str(exc.stderr or "") + "\nverifier timed out"
        raw_root = self.artifact_path.parent / f"{self.artifact_path.stem}-raw" / run_name
        raw_root.mkdir(parents=True, exist_ok=True)
        stdout_path = raw_root / "verifier.stdout.log"
        stderr_path = raw_root / "verifier.stderr.log"
        stdout_path.write_text(verifier_stdout, encoding="utf-8")
        stderr_path.write_text(verifier_stderr, encoding="utf-8")
        if task_state is not None:
            parent_trace = agent.run_store.trace_path(task_state)
            parent_calls = task_state.attempts
            tool_steps = task_state.tool_steps
            stop_reason = task_state.stop_reason
        else:
            parent_trace = None
            parent_calls = 0
            tool_steps = 0
            stop_reason = "agent_error"
        trace_metrics = _trace_metrics(copy_root, parent_trace)
        if trace_metrics["llm_calls"] == 0 and parent_calls:
            trace_metrics["llm_calls"] = parent_calls
            trace_metrics["parent_llm_calls"] = parent_calls
        expected_paths_exist = all((copy_root / path).exists() for path in task["expected_paths"])
        verifier_passed = verifier_exit_code == 0
        within_budget = task_state is not None and tool_steps <= task["step_budget"]
        final_returned = (
            task_state is not None
            and stop_reason == STOP_REASON_FINAL_ANSWER_RETURNED
            and not agent_error
        )
        passed = verifier_passed and expected_paths_exist and within_budget and final_returned
        if passed:
            failure_category = None
        elif agent_error:
            failure_category = "agent_or_model_error"
        elif verifier_exit_code == 124:
            failure_category = "verifier_timeout"
        elif not verifier_passed:
            failure_category = "verifier_failed"
        elif not expected_paths_exist:
            failure_category = "missing_expected_path"
        elif not within_budget:
            failure_category = "step_budget_exceeded"
        elif not final_returned:
            failure_category = "non_final_stop"
        else:
            failure_category = "unknown"
        total_seconds = time.monotonic() - task_started
        return {
            "id": task["id"],
            "repo_id": task["repo_id"],
            "category": task["category"],
            "repetition": repetition,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "failure_category": failure_category,
            "prompt": task["prompt"],
            "step_budget": task["step_budget"],
            "tool_steps": tool_steps,
            "within_budget": within_budget,
            "verifier": task["verifier"],
            "verifier_exit_code": verifier_exit_code,
            "verifier_passed": verifier_passed,
            "expected_paths_exist": expected_paths_exist,
            "final_returned": final_returned,
            "stop_reason": stop_reason,
            "agent_error": agent_error,
            "final_answer": final_answer,
            "agent_seconds": round(agent_seconds, 6),
            "task_seconds": round(total_seconds, 6),
            "workspace_copy": str(copy_root),
            "verifier_stdout_artifact": str(stdout_path.resolve()),
            "verifier_stderr_artifact": str(stderr_path.resolve()),
            **trace_metrics,
        }

    @staticmethod
    def _summarize(rows, inventories):
        task_count = len(rows)
        completed = sum(1 for row in rows if row["passed"])
        repo_ids = sorted({row["repo_id"] for row in rows})
        categories = sorted({row["category"] for row in rows})
        failure_categories = Counter(
            row["failure_category"] for row in rows if row["failure_category"]
        )
        return {
            "repository_count": len(inventories),
            "source_files": sum(item["source_files"] for item in inventories),
            "physical_loc": sum(item["physical_loc"] for item in inventories),
            "task_count": task_count,
            "completed": completed,
            "failed": task_count - completed,
            "failure_categories": dict(failure_categories),
            "completion_rate": completed / task_count if task_count else 0.0,
            "completion_rate_ci95": _wilson_interval(completed, task_count),
            "avg_llm_calls": _mean(row["llm_calls"] for row in rows),
            "avg_parent_llm_calls": _mean(row["parent_llm_calls"] for row in rows),
            "avg_child_llm_calls": _mean(row["child_llm_calls"] for row in rows),
            "avg_tool_calls": _mean(row["tool_calls"] for row in rows),
            "avg_task_seconds": _mean(row["task_seconds"] for row in rows),
            "p95_task_seconds": _percentile((row["task_seconds"] for row in rows), 0.95),
            "policy_rejections": sum(row["policy_rejections"] for row in rows),
            "security_events": dict(
                sum((Counter(row["security_events"]) for row in rows), Counter())
            ),
            "token_usage_coverage": _mean(row["token_usage_coverage"] for row in rows),
            "by_repository": {
                repo_id: _completion_group(
                    row for row in rows if row["repo_id"] == repo_id
                )
                for repo_id in repo_ids
            },
            "by_category": {
                category: _completion_group(
                    row for row in rows if row["category"] == category
                )
                for category in categories
            },
        }


def _default_engine_factory(root, repository):
    del repository
    from .embeddings import FastEmbedEncoder, encoder_from_env
    from .retrieval import RetrievalEngine

    encoder = encoder_from_env()
    if isinstance(encoder, FastEmbedEncoder) and encoder.cache_dir is None:
        encoder.cache_dir = str((Path.cwd() / ".bingo" / "retrieval" / "models").resolve())
    return RetrievalEngine(root, encoder=encoder)


def _mode_metrics(rows):
    answerable = [row for row in rows if row["expected"]]
    negative = [row for row in rows if not row["expected"]]
    latencies = [sample for row in rows for sample in row["duration_samples_ms"]]
    repo_ids = sorted({row["repo_id"] for row in rows})
    per_repo_recall = []
    per_repo_mrr = []
    per_repository = {}
    for repo_id in repo_ids:
        repo_rows = [row for row in answerable if row["repo_id"] == repo_id]
        repo_negative = [row for row in negative if row["repo_id"] == repo_id]
        if repo_rows:
            per_repo_recall.append(_mean(row["recall_at_5"] for row in repo_rows))
            per_repo_mrr.append(_mean(row["reciprocal_rank"] for row in repo_rows))
        repo_latencies = [
            sample
            for row in rows
            if row["repo_id"] == repo_id
            for sample in row["duration_samples_ms"]
        ]
        per_repository[repo_id] = {
            "query_count": len(repo_rows) + len(repo_negative),
            "recall_at_5": _mean(row["recall_at_5"] for row in repo_rows),
            "mrr": _mean(row["reciprocal_rank"] for row in repo_rows),
            "no_answer_accuracy": _mean(
                row["correct_abstention"] for row in repo_negative
            ),
            "p95_ms": _percentile(repo_latencies, 0.95),
        }
    return {
        "query_count": len(rows),
        "answerable_queries": len(answerable),
        "negative_queries": len(negative),
        "recall_at_5": _mean(row["recall_at_5"] for row in answerable),
        "mrr": _mean(row["reciprocal_rank"] for row in answerable),
        "macro_recall_at_5": _mean(per_repo_recall),
        "macro_mrr": _mean(per_repo_mrr),
        "no_answer_accuracy": _mean(row["correct_abstention"] for row in negative),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "per_repository": per_repository,
        "rows": rows,
    }


def _comparison(candidate, baseline):
    recall_delta = candidate["recall_at_5"] - baseline["recall_at_5"]
    mrr_delta = candidate["mrr"] - baseline["mrr"]
    p95_delta = baseline["p95_ms"] - candidate["p95_ms"]
    return {
        "recall_at_5_delta_points": round(recall_delta * 100, 4),
        "recall_at_5_relative_improvement_percent": round(
            recall_delta / baseline["recall_at_5"] * 100, 4
        ) if baseline["recall_at_5"] else None,
        "mrr_delta": round(mrr_delta, 6),
        "mrr_relative_improvement_percent": round(
            mrr_delta / baseline["mrr"] * 100, 4
        ) if baseline["mrr"] else None,
        "p95_delta_ms": round(p95_delta, 4),
        "p95_reduction_percent": round(
            p95_delta / baseline["p95_ms"] * 100, 4
        ) if baseline["p95_ms"] else None,
    }


class RetrievalAblationEvaluator:
    """Compare retrieval modes on grounded queries from real repository snapshots."""

    def __init__(
        self,
        manifest_path,
        *,
        artifact_path,
        workspace_root,
        modes=("vector", "hybrid", "auto"),
        latency_repetitions=5,
        engine_factory=None,
        require_vector=True,
    ):
        self.manifest_path = Path(manifest_path)
        self.artifact_path = Path(artifact_path).resolve()
        self.workspace_root = Path(workspace_root).resolve()
        self.modes = tuple(modes)
        self.latency_repetitions = int(latency_repetitions)
        self.engine_factory = engine_factory or _default_engine_factory
        self.require_vector = bool(require_vector)
        if not self.modes or self.latency_repetitions < 1:
            raise ValueError("retrieval modes and positive repetitions are required")

    def run(self):
        manifest = load_real_benchmark(self.manifest_path)
        inventories = [inventory_repository(item) for item in manifest["repositories"]]
        all_rows = {mode: [] for mode in self.modes}
        indexes = []
        for repository in manifest["repositories"]:
            if not repository["queries"]:
                continue
            root = self.workspace_root / repository["id"]
            if root.exists():
                shutil.rmtree(root)
            root.parent.mkdir(parents=True, exist_ok=True)
            _copy_repository(repository["path"], root, repository.get("exclude", []))
            engine = self.engine_factory(root, repository)
            try:
                started = time.monotonic()
                index = engine.index(complete=True)
                index_seconds = time.monotonic() - started
                indexes.append(
                    {"repo_id": repository["id"], "index_seconds": index_seconds, **index}
                )
                if self.require_vector and (
                    index.get("fallback_reason") or float(index.get("vector_coverage", 0)) < 1
                ):
                    raise RuntimeError(
                        f"repository {repository['id']} does not have complete real vectors"
                    )
                for mode in self.modes:
                    if hasattr(engine, "_query_vectors"):
                        engine._query_vectors.clear()
                    for query in repository["queries"]:
                        engine.search(query["query"], mode=mode, top_k=5)
                        samples = []
                        result = None
                        for _ in range(self.latency_repetitions):
                            result = engine.search(query["query"], mode=mode, top_k=5)
                            samples.append(float(result.get("duration_ms", 0.0)))
                        ranks = [
                            {source.get("path", "") for source in hit.get("sources", [])}
                            for hit in result.get("hits", [])
                        ]
                        expected = set(query["paths"])
                        found = set().union(*ranks) if ranks else set()
                        rank = next(
                            (index for index, paths in enumerate(ranks, 1) if paths & expected),
                            0,
                        )
                        all_rows[mode].append(
                            {
                                "repo_id": repository["id"],
                                "id": query["id"],
                                "query": query["query"],
                                "kind": query["kind"],
                                "expected": sorted(expected),
                                "found": sorted(found),
                                "recall_at_5": (
                                    len(found & expected) / len(expected) if expected else None
                                ),
                                "reciprocal_rank": 1 / rank if rank else 0.0,
                                "correct_abstention": not found if not expected else None,
                                "duration_samples_ms": samples,
                                "strategy": result.get("strategy", ""),
                                "fallback_reason": result.get("fallback_reason", ""),
                                "channels_used": result.get("channels_used", []),
                            }
                        )
            finally:
                engine.close()
        modes = {mode: _mode_metrics(rows) for mode, rows in all_rows.items()}
        comparisons = {}
        if "vector" in modes:
            for mode in ("hybrid", "auto"):
                if mode in modes:
                    comparisons[f"{mode}_vs_vector"] = _comparison(modes[mode], modes["vector"])
        query_count = sum(len(repo["queries"]) for repo in manifest["repositories"])
        artifact = {
            "schema_version": REAL_BENCHMARK_SCHEMA_VERSION,
            "artifact_type": "real-repository-retrieval-v1",
            "artifact_path": str(self.artifact_path),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "manifest_path": str(Path(manifest["manifest_path"])),
            "protocol": {
                "top_k": 5,
                "warmup_per_query": 1,
                "latency_repetitions": self.latency_repetitions,
                "modes": list(self.modes),
                "accuracy_aggregation": "micro mean plus equal-weight per-repository macro mean",
                "latency": "warmed query latency; index time reported separately",
            },
            "repositories": inventories,
            "indexes": indexes,
            "summary": {
                "repository_count": sum(1 for repo in manifest["repositories"] if repo["queries"]),
                "source_files": sum(item["source_files"] for item in inventories),
                "physical_loc": sum(item["physical_loc"] for item in inventories),
                "query_count": query_count,
            },
            "modes": modes,
            "comparisons": comparisons,
        }
        _write_json(self.artifact_path, artifact)
        return artifact


def _format_scale(value, suffix):
    value = float(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} M {suffix}"
    if value >= 1_000:
        return f"{value / 1_000:.1f} K {suffix}"
    return f"{int(value)} {suffix}"


def render_real_benchmark_report(e2e, retrieval, output_path):
    """Render a resume-safe report while keeping protocol limitations visible."""
    e2e_summary = e2e.get("summary", {}) if e2e else {}
    retrieval_summary = retrieval.get("summary", {}) if retrieval else {}
    repo_count = max(
        int(e2e_summary.get("repository_count", 0)),
        int(retrieval_summary.get("repository_count", 0)),
    )
    files = max(
        int(e2e_summary.get("source_files", 0)),
        int(retrieval_summary.get("source_files", 0)),
    )
    loc = max(
        int(e2e_summary.get("physical_loc", 0)),
        int(retrieval_summary.get("physical_loc", 0)),
    )
    lines = [
        "# Bingo 真实仓库验证报告",
        "",
        f"验证规模：**{repo_count} 个真实仓库**，{_format_scale(files, '源文件')}，{_format_scale(loc, 'LoC')}。",
        "",
        "仓库必须记录固定 commit；无法获得 Git commit 时使用完整内容 SHA-256 快照。源文件与 LoC 排除依赖、构建产物、生成缓存和 Bingo 运行目录。",
        "",
        "## 端到端 Agent 结果",
        "",
    ]
    e2e_revisions = {
        item.get("id"): item.get("revision") for item in (e2e or {}).get("repositories", [])
    }
    retrieval_revisions = {
        item.get("id"): item.get("revision")
        for item in (retrieval or {}).get("repositories", [])
    }
    mismatched_revisions = sorted(
        repo_id
        for repo_id in set(e2e_revisions) & set(retrieval_revisions)
        if e2e_revisions[repo_id] != retrieval_revisions[repo_id]
    )
    if mismatched_revisions:
        lines.extend(
            [
                "**警告：端到端与检索工件的仓库 revision 不一致，不能把两组结果合并成同一次实验。**",
                "",
                "不一致仓库：`" + "`, `".join(mismatched_revisions) + "`。",
                "",
            ]
        )
    if e2e_summary:
        model_name = e2e.get("reproducibility", {}).get("model_name", "unknown")
        lines.extend(
            [
                f"- 共执行 **{e2e_summary.get('task_count', 0)}** 个任务，完成 **{e2e_summary.get('completed', 0)}** 个，任务完成率 **{float(e2e_summary.get('completion_rate', 0)):.1%}**。",
                f"- 平均 LLM 调用 **{float(e2e_summary.get('avg_llm_calls', 0)):.2f} 次/任务**，平均耗时 **{float(e2e_summary.get('avg_task_seconds', 0)):.2f} s**，P95 **{float(e2e_summary.get('p95_task_seconds', 0)):.2f} s**。",
                f"- 模型：`{model_name}`；重复次数：`{e2e.get('reproducibility', {}).get('repetitions', 0)}`。",
            ]
        )
        if model_name in {"fake", "scripted-smoke"}:
            lines.append(
                "- **Harness smoke：本组使用确定性脚本输出，只验证评测链路，不是 LLM 能力成绩，不可写入简历。**"
            )
        failure_categories = dict(e2e_summary.get("failure_categories", {}))
        if not failure_categories and e2e.get("rows"):
            failure_categories = dict(
                Counter(
                    row.get("failure_category")
                    or ("agent_or_model_error" if row.get("agent_error") else "unknown")
                    for row in e2e["rows"]
                    if not row.get("passed")
                )
            )
        if failure_categories:
            rendered_failures = "，".join(
                f"{name}={count}" for name, count in sorted(failure_categories.items())
            )
            lines.append(f"- 失败分类：`{rendered_failures}`。")
        if failure_categories.get("agent_or_model_error") == e2e_summary.get(
            "task_count"
        ):
            lines.append(
                "- **本轮所有任务均在 Agent/模型调用阶段失败，完成率不能用于评价模型编码能力。**"
            )
    else:
        lines.append("- 未运行。没有模型结果时，报告不会生成任务完成率占位数字。")
    lines.extend(["", "## 检索消融", ""])
    modes = retrieval.get("modes", {}) if retrieval else {}
    for name in ("vector", "hybrid", "auto"):
        if name not in modes:
            continue
        item = modes[name]
        no_answer_accuracy = float(item.get("no_answer_accuracy", 0.0))
        lines.append(
            f"- {name.title()}：Recall@5 `{item['recall_at_5']:.3f}`，MRR `{item['mrr']:.3f}`，P95 `{item['p95_ms']:.2f} ms`，无答案准确率 `{no_answer_accuracy:.3f}`。"
        )
    comparison = (retrieval.get("comparisons", {}) if retrieval else {}).get(
        "hybrid_vs_vector"
    )
    if comparison:
        mrr_value = comparison["mrr_relative_improvement_percent"]
        p95_value = comparison["p95_reduction_percent"]
        mrr_text = (
            f"MRR 相对提升 **{mrr_value:.2f}%**"
            if mrr_value >= 0
            else f"MRR 相对下降 **{abs(mrr_value):.2f}%**"
        )
        p95_text = (
            f"P95 降低 **{p95_value:.2f}%**"
            if p95_value >= 0
            else f"P95 上升 **{abs(p95_value):.2f}%**"
        )
        lines.append(
            f"- Hybrid 相比纯 Vector：Recall@5 变化 **{comparison['recall_at_5_delta_points']:+.2f} 个百分点**，{mrr_text}，{p95_text}。"
        )
    lines.extend(
        [
            "",
            "## 证据与口径",
            "",
            "- 任务成功只由 verifier、产物存在性、正常停止原因和步骤预算共同判定，模型不能自行宣布成功。",
            "- P95 使用每条查询预热一次后的重复测量；索引耗时与查询耗时分开记录。",
            "- 跨仓库同时报告 micro 与等权 macro 指标，避免大仓库支配准确率。",
            "- 变异修复任务、历史真实 issue 和合成干扰数据必须分别标注，不能混写成真实线上任务。",
            "- 原始 JSON、Verifier stdout/stderr、隔离工作区和 Agent Trace 全部保留，可回溯每个结论。",
            "",
            "## 工件",
            "",
            f"- 端到端原始 JSON：`{e2e.get('artifact_path', '未运行') if e2e else '未运行'}`",
            f"- 检索原始 JSON：`{retrieval.get('artifact_path', '未运行') if retrieval else '未运行'}`",
            "",
            "只有真实模型、固定仓库 revision、确定性 verifier 和足够任务量同时满足时，端到端结果才适合写入简历。小样本 smoke 结果只用于证明评测链路能够运行。",
            "",
        ]
    )
    text = "\n".join(lines)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return text
