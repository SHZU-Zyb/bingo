"""Multi-turn conversation benchmark evaluation."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import FakeModelClient
from .run_store import RunStore
from .runtime import Bingo, SessionStore
from .task_state import STOP_REASON_FINAL_ANSWER_RETURNED
from .workspace import WorkspaceContext

MULTI_TURN_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_MULTI_TURN_BENCHMARK_PATH = Path("benchmarks/multi_turn_tasks.json")
REQUIRED_TASK_KEYS = (
    "id",
    "fixture_repo",
    "allowed_tools",
    "total_step_budget",
    "turns",
    "final_verifier",
)


def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def summarize_conversation_rows(rows):
    rows = list(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    context_hits = sum(int(row.get("context_hits", 0)) for row in rows)
    context_targets = sum(int(row.get("context_targets", 0)) for row in rows)
    return {
        "total_conversations": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "context_hits": context_hits,
        "context_targets": context_targets,
        "conversation_completion_rate": _safe_ratio(passed, len(rows)),
        "context_hit_rate": _safe_ratio(context_hits, context_targets),
    }


def validate_multi_turn_benchmark(data, repo_root=None):
    if not isinstance(data, dict):
        raise ValueError("benchmark must be a mapping")
    if int(data.get("schema_version", 0)) != MULTI_TURN_BENCHMARK_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")

    repo_root = Path(repo_root or Path.cwd()).resolve()
    normalized_tasks = []
    seen_task_ids = set()
    for index, original_task in enumerate(tasks):
        if not isinstance(original_task, dict):
            raise ValueError(f"task at index {index} must be a mapping")
        missing = [key for key in REQUIRED_TASK_KEYS if key not in original_task]
        if missing:
            raise ValueError(f"task at index {index} is missing required keys: {', '.join(missing)}")

        task = dict(original_task)
        task_id = str(task["id"]).strip()
        if not task_id or task_id in seen_task_ids:
            raise ValueError(f"invalid or duplicate task id: {task_id!r}")
        seen_task_ids.add(task_id)

        fixture_repo = str(task["fixture_repo"]).strip()
        if not fixture_repo or not (repo_root / fixture_repo).is_dir():
            raise ValueError(f"task {task_id} fixture_repo does not exist: {fixture_repo}")

        allowed_tools = task["allowed_tools"]
        if not isinstance(allowed_tools, list) or not allowed_tools:
            raise ValueError(f"task {task_id} allowed_tools must be a non-empty list")
        normalized_tools = [str(name).strip() for name in allowed_tools]
        if any(not name for name in normalized_tools):
            raise ValueError(f"task {task_id} allowed_tools contains an empty name")

        total_step_budget = int(task["total_step_budget"])
        if total_step_budget < 1:
            raise ValueError(f"task {task_id} total_step_budget must be positive")

        turns = task["turns"]
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"task {task_id} turns must be a non-empty list")
        normalized_turns = []
        seen_turn_ids = set()
        for turn_index, original_turn in enumerate(turns):
            if not isinstance(original_turn, dict):
                raise ValueError(f"task {task_id} turn {turn_index} must be a mapping")
            turn = dict(original_turn)
            turn_id = str(turn.get("id", "")).strip()
            user = str(turn.get("user", "")).strip()
            if not turn_id or turn_id in seen_turn_ids:
                raise ValueError(f"task {task_id} has an invalid or duplicate turn id")
            if not user:
                raise ValueError(f"task {task_id} turn {turn_id} user must not be empty")
            seen_turn_ids.add(turn_id)

            answer_contains = turn.get("answer_contains", [])
            if not isinstance(answer_contains, list):
                raise ValueError(f"task {task_id} turn {turn_id} answer_contains must be a list")
            normalized_answer_checks = [str(value).strip() for value in answer_contains]
            if any(not value for value in normalized_answer_checks):
                raise ValueError(f"task {task_id} turn {turn_id} answer_contains has an empty value")

            required_context = turn.get("required_context", [])
            if not isinstance(required_context, list):
                raise ValueError(f"task {task_id} turn {turn_id} required_context must be a list")
            normalized_context = []
            for target in required_context:
                if not isinstance(target, dict):
                    raise ValueError(f"task {task_id} turn {turn_id} required_context must contain mappings")
                target_id = str(target.get("id", "")).strip()
                target_text = str(target.get("text", "")).strip()
                if not target_id or not target_text:
                    raise ValueError(f"task {task_id} turn {turn_id} required_context needs id and text")
                normalized_context.append({"id": target_id, "text": target_text})

            turn.update(
                {
                    "id": turn_id,
                    "user": user,
                    "answer_contains": normalized_answer_checks,
                    "required_context": normalized_context,
                }
            )
            normalized_turns.append(turn)

        final_verifier = str(task["final_verifier"]).strip()
        if not final_verifier:
            raise ValueError(f"task {task_id} final_verifier must not be empty")

        scripted_outputs = task.get("scripted_outputs", [])
        if scripted_outputs and (
            not isinstance(scripted_outputs, list)
            or any(not str(output).strip() for output in scripted_outputs)
        ):
            raise ValueError(f"task {task_id} scripted_outputs must contain non-empty strings")

        task.update(
            {
                "id": task_id,
                "fixture_repo": fixture_repo,
                "allowed_tools": normalized_tools,
                "total_step_budget": total_step_budget,
                "turns": normalized_turns,
                "final_verifier": final_verifier,
                "scripted_outputs": [str(output) for output in scripted_outputs],
            }
        )
        normalized_tasks.append(task)

    return {
        **data,
        "schema_version": MULTI_TURN_BENCHMARK_SCHEMA_VERSION,
        "tasks": normalized_tasks,
    }


def load_multi_turn_benchmark(path=DEFAULT_MULTI_TURN_BENCHMARK_PATH, repo_root=None):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if repo_root is None:
        repo_root = path.resolve().parent.parent
    return validate_multi_turn_benchmark(data, repo_root=repo_root)


def _load_trace(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normalize_text(value):
    return " ".join(str(value).casefold().replace("|", " ").split())


def _rendered_relevant_notes(trace):
    notes = []
    for event in trace:
        if event.get("event") != "prompt_built":
            continue
        prompt_metadata = event.get("prompt_metadata", {})
        relevant_memory = prompt_metadata.get("relevant_memory", {})
        for note in relevant_memory.get("rendered_notes", []):
            text = str(note).strip()
            if text and text not in notes:
                notes.append(text)
    return notes


def _score_context_targets(targets, rendered_notes):
    normalized_notes = [_normalize_text(note) for note in rendered_notes]
    results = []
    for target in targets:
        normalized_target = _normalize_text(target["text"])
        hit = any(normalized_target in note for note in normalized_notes)
        results.append({"id": target["id"], "text": target["text"], "hit": hit})
    return results


def _portable_verifier_command(command):
    py3_path = shutil.which("python3") or ""
    if py3_path and "WindowsApps" not in py3_path:
        python_command = py3_path
    else:
        python_command = shutil.which("python") or "python3"
    return str(command).replace("python3", python_command)


class MultiTurnBenchmarkEvaluator:
    def __init__(
        self,
        benchmark_path=DEFAULT_MULTI_TURN_BENCHMARK_PATH,
        artifact_path=Path("artifacts/multi-turn-benchmark.json"),
        workspace_root=None,
        model_client_factory=None,
        max_new_tokens=128,
    ):
        self.benchmark_path = Path(benchmark_path)
        self.artifact_path = Path(artifact_path)
        self.workspace_root = Path(workspace_root) if workspace_root is not None else Path(
            tempfile.mkdtemp(prefix="bingo-multi-turn-")
        )
        self.model_client_factory = model_client_factory
        self.max_new_tokens = int(max_new_tokens)
        self.repo_root = self.benchmark_path.resolve().parent.parent

    def load(self):
        return load_multi_turn_benchmark(self.benchmark_path, repo_root=self.repo_root)

    def run(self):
        benchmark = self.load()
        rows = [self.run_task(task) for task in benchmark["tasks"]]
        artifact = {
            "schema_version": MULTI_TURN_BENCHMARK_SCHEMA_VERSION,
            "benchmark": {
                "source": str(self.benchmark_path),
                "task_count": len(rows),
            },
            "summary": summarize_conversation_rows(rows),
            "rows": rows,
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return artifact

    def run_task(self, task):
        task = dict(task)
        fixture_source = self.repo_root / task["fixture_repo"]
        fixture_copy_root = self.workspace_root / task["id"] / fixture_source.name
        if fixture_copy_root.exists():
            shutil.rmtree(fixture_copy_root)
        fixture_copy_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fixture_source, fixture_copy_root)

        workspace = WorkspaceContext.build(
            fixture_copy_root,
            repo_root_override=fixture_copy_root,
        )
        session_store = SessionStore(fixture_copy_root / ".bingo" / "sessions")
        run_store = RunStore(fixture_copy_root / ".bingo" / "runs")
        if self.model_client_factory is None:
            if not task.get("scripted_outputs"):
                raise ValueError(f"task {task['id']} needs scripted_outputs or a model_client_factory")
            model_client = FakeModelClient(task["scripted_outputs"])
        else:
            model_client = self.model_client_factory(task=task, workspace=workspace)

        agent = Bingo(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            run_store=run_store,
            approval_policy="auto",
            max_steps=int(task["total_step_budget"]),
            max_new_tokens=self.max_new_tokens,
        )
        unknown_tools = set(task["allowed_tools"]) - set(agent.tools)
        if unknown_tools:
            raise ValueError(f"task {task['id']} declares unknown tools: {sorted(unknown_tools)}")
        agent.tools = {name: agent.tools[name] for name in task["allowed_tools"]}
        agent.prefix_state = agent.build_prefix()
        agent.prefix = agent.prefix_state.text

        turn_rows = []
        total_tool_steps = 0
        context_hits = 0
        context_targets = 0
        for turn in task["turns"]:
            answer = agent.ask(turn["user"])
            state = agent.current_task_state
            trace = _load_trace(agent.run_store.trace_path(state.run_id))
            tool_events = [event for event in trace if event.get("event") == "tool_executed"]
            answer_text = str(answer).casefold()
            answer_checks_passed = all(
                str(expected).casefold() in answer_text for expected in turn.get("answer_contains", [])
            )
            normal_stop = state.stop_reason == STOP_REASON_FINAL_ANSWER_RETURNED
            required_context = list(turn.get("required_context", []))
            rendered_notes = _rendered_relevant_notes(trace)
            # Context quality is about what the model actually received.  After
            # separating memory, history and source evidence, a required fact may
            # legitimately live outside recalled memory.
            prompts = list(getattr(agent.model_client, "prompts", []))
            rendered_context = [prompts[-1]] if prompts else rendered_notes
            context_target_results = _score_context_targets(required_context, rendered_context)
            turn_context_hits = sum(1 for result in context_target_results if result["hit"])
            context_hits += turn_context_hits
            context_targets += len(required_context)
            turn_rows.append(
                {
                    "id": turn["id"],
                    "user": turn["user"],
                    "answer": answer,
                    "run_id": state.run_id,
                    "session_id": agent.session["id"],
                    "passed": answer_checks_passed and normal_stop,
                    "answer_checks_passed": answer_checks_passed,
                    "stop_reason": state.stop_reason,
                    "tool_steps": state.tool_steps,
                    "context_hits": turn_context_hits,
                    "context_targets": len(required_context),
                    "context_target_results": context_target_results,
                    "rendered_relevant_notes": rendered_notes,
                    "tool_names": [event.get("name", "") for event in tool_events],
                    "tool_error_codes": [event.get("tool_error_code", "") for event in tool_events],
                }
            )
            total_tool_steps += state.tool_steps

        verifier = subprocess.run(
            _portable_verifier_command(task["final_verifier"]),
            cwd=fixture_copy_root,
            shell=True,
            capture_output=True,
            text=True,
        )
        verifier_passed = verifier.returncode == 0
        within_budget = total_tool_steps <= int(task["total_step_budget"])
        passed = all(turn["passed"] for turn in turn_rows) and verifier_passed and within_budget
        return {
            "id": task["id"],
            "session_id": agent.session["id"],
            "passed": passed,
            "turns": turn_rows,
            "total_tool_steps": total_tool_steps,
            "within_budget": within_budget,
            "context_hits": context_hits,
            "context_targets": context_targets,
            "verifier_passed": verifier_passed,
            "verifier_exit_code": verifier.returncode,
            "verifier_stdout": verifier.stdout,
            "verifier_stderr": verifier.stderr,
            "fixture_copy_root": str(fixture_copy_root),
        }
