"""Bounded templates for useful multi-Agent work and local verification."""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath

from .verification_parser import needs_diagnostic_agent, parse_verification_output
from .workflow_types import WorkflowNode, WorkflowRecord

NODE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def _clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _compact_agent_report(node_id, raw, artifact_ref):
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"summary": raw}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = {"summary": str(raw)}
    findings = []
    for item in list(parsed.get("findings", []))[:5]:
        if isinstance(item, dict):
            findings.append(
                {
                    key: _clip(value, 500)
                    for key, value in item.items()
                    if key in {"severity", "message", "root_cause", "recommended_action"}
                }
            )
        else:
            findings.append({"message": _clip(item, 500)})
    return {
        "node_id": node_id,
        "status": "completed",
        "summary": _clip(parsed.get("summary", parsed.get("root_cause", "completed")), 800),
        "findings": findings,
        "evidence_refs": [_clip(item, 300) for item in list(parsed.get("evidence_refs", []))[:10]],
        "artifact_ref": artifact_ref,
    }


def _run_local_command(**kwargs):
    command = kwargs.pop("command")
    kwargs.pop("check", None)
    return subprocess.run(command, check=False, **kwargs)


class WorkflowEngine:
    def __init__(
        self,
        workspace_root,
        store,
        *,
        parallel_runner=None,
        diagnosis_runner=None,
        command_runner=None,
        shell_env=None,
    ):
        self.workspace_root = workspace_root
        self.store = store
        self.parallel_runner = parallel_runner
        self.diagnosis_runner = diagnosis_runner
        self.command_runner = command_runner or _run_local_command
        self.shell_env = shell_env

    def run_parallel(self, objective, branches, *, max_parallel=3):
        branches = self._validate_branches(branches)
        if not 2 <= int(max_parallel) <= 4:
            raise ValueError("parallel workflow requires at least two workers and at most four")
        if self.parallel_runner is None:
            raise RuntimeError("parallel Agent runner is unavailable")
        workflow_id = self.store.create("parallel_explore", objective)
        record = WorkflowRecord(
            workflow_id=workflow_id,
            workflow_type="parallel_explore",
            objective=str(objective),
            status="running",
            nodes=[
                WorkflowNode(item["id"], "subagent", item["task"], tuple(item["scope"]))
                for item in branches
            ],
        )
        reports = []
        workers = min(max(1, int(max_parallel)), 4, len(branches))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bingo-workflow") as pool:
            future_map = {
                pool.submit(
                    self.parallel_runner,
                    {
                        "workflow_id": workflow_id,
                        "node_id": item["id"],
                        "objective": str(objective),
                        "task": item["task"],
                        "scope": item["scope"],
                        "output_contract": "Return summary, findings, and evidence_refs only.",
                    },
                ): item
                for item in branches
            }
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    raw = future.result()
                    serialized = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                    artifact = self.store.write_text(workflow_id, item["id"], "result.json", serialized)
                    report = _compact_agent_report(item["id"], raw, artifact["artifact_ref"])
                except Exception as exc:  # noqa: BLE001 - one failed branch must not cancel its siblings
                    artifact = self.store.write_text(workflow_id, item["id"], "error.log", str(exc))
                    report = {
                        "node_id": item["id"],
                        "status": "failed",
                        "summary": _clip(exc, 800),
                        "findings": [],
                        "evidence_refs": [],
                        "artifact_ref": artifact["artifact_ref"],
                    }
                reports.append(report)
        reports.sort(key=lambda item: item["node_id"])
        status = "completed" if all(item["status"] == "completed" for item in reports) else "partial_failed"
        record.status = status
        for node in record.nodes:
            report = next(item for item in reports if item["node_id"] == node.node_id)
            node.status = report["status"]
            node.artifact_refs = [report["artifact_ref"]]
        state = record.to_dict()
        state["reports"] = reports
        self.store.write_json(workflow_id, "workflow.json", state)
        return {"workflow_id": workflow_id, "workflow_type": record.workflow_type, "status": status, "reports": reports}

    def run_verification(self, objective, command, *, timeout=120):
        command = str(command).strip()
        if not command:
            raise ValueError("verification command must not be empty")
        timeout = int(timeout)
        if not 1 <= timeout <= 300:
            raise ValueError("verification timeout must be in [1,300]")
        workflow_id = self.store.create("verify_and_diagnose", objective)
        try:
            completed = self.command_runner(
                command=command,
                cwd=self.workspace_root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=self.shell_env,
            )
            exit_code = int(completed.returncode)
            stdout, stderr = str(completed.stdout or ""), str(completed.stderr or "")
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "") + f"\nverification timed out after {timeout}s"
        stdout_ref = self.store.write_text(workflow_id, "runner", "stdout.log", stdout)
        stderr_ref = self.store.write_text(workflow_id, "runner", "stderr.log", stderr)
        verification = parse_verification_output(command, stdout, stderr, exit_code)
        decision = needs_diagnostic_agent(verification)
        diagnosis = {"invoked": False, "required": decision["required"], "reasons": decision["reasons"]}
        if decision["required"] and self.diagnosis_runner is not None:
            packet = {
                "workflow_id": workflow_id,
                "objective": str(objective),
                "verification": verification,
                "artifact_refs": [stdout_ref["artifact_ref"], stderr_ref["artifact_ref"]],
                "output_contract": "Return root cause, confidence, evidence_refs, and recommended action.",
            }
            try:
                raw_diagnosis = self.diagnosis_runner(packet)
                serialized = raw_diagnosis if isinstance(raw_diagnosis, str) else json.dumps(raw_diagnosis, ensure_ascii=False)
                diagnosis_ref = self.store.write_text(workflow_id, "diagnostician", "result.json", serialized)
                compact = _compact_agent_report("diagnostician", raw_diagnosis, diagnosis_ref["artifact_ref"])
                diagnosis = {
                    "invoked": True,
                    "required": True,
                    "reasons": decision["reasons"],
                    **{key: value for key, value in compact.items() if key != "node_id"},
                }
                if isinstance(raw_diagnosis, dict):
                    diagnosis["root_cause"] = _clip(raw_diagnosis.get("root_cause", ""), 800)
                    diagnosis["confidence"] = raw_diagnosis.get("confidence")
            except Exception as exc:  # noqa: BLE001 - retain local facts when model diagnosis fails
                error_ref = self.store.write_text(workflow_id, "diagnostician", "error.log", str(exc))
                diagnosis = {
                    "invoked": True,
                    "required": True,
                    "status": "failed",
                    "reasons": decision["reasons"],
                    "summary": _clip(exc, 800),
                    "artifact_ref": error_ref["artifact_ref"],
                }
        result = {
            "workflow_id": workflow_id,
            "workflow_type": "verify_and_diagnose",
            "status": verification["status"],
            "verification": verification,
            "diagnosis": diagnosis,
            "artifacts": {
                "stdout": stdout_ref["artifact_ref"],
                "stderr": stderr_ref["artifact_ref"],
            },
        }
        self.store.write_json(workflow_id, "workflow.json", result)
        return result

    @staticmethod
    def _validate_branches(branches):
        if not isinstance(branches, list) or len(branches) < 2:
            raise ValueError("parallel workflow requires at least two branches")
        if len(branches) > 4:
            raise ValueError("parallel workflow supports at most four branches")
        normalized, ids, claimed_scopes = [], set(), []
        for raw in branches:
            if not isinstance(raw, dict):
                raise TypeError("workflow branch must be an object")
            node_id = str(raw.get("id", "")).strip().lower()
            task = str(raw.get("task", "")).strip()
            scope = [str(item).strip().replace("\\", "/") for item in raw.get("scope", []) if str(item).strip()]
            if not NODE_ID_PATTERN.fullmatch(node_id) or not task or not scope:
                raise ValueError("each branch requires a valid id, task, and scope")
            if node_id in ids:
                raise ValueError("parallel branches require unique ids")
            scope_parts = [tuple(PurePosixPath(path).parts) for path in scope]
            if any(
                left[: len(right)] == right or right[: len(left)] == left
                for left in scope_parts
                for right in claimed_scopes
            ):
                raise ValueError("parallel branch scopes overlap")
            ids.add(node_id)
            claimed_scopes.extend(scope_parts)
            normalized.append({"id": node_id, "task": task, "scope": scope})
        return normalized
