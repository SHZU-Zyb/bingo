"""Lossless workflow artifacts with bounded, path-safe reads."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path

SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkflowStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, workflow_type, objective):
        workflow_id = "wf_" + uuid.uuid4().hex[:10]
        path = self.root / workflow_id
        path.mkdir(parents=True)
        self.write_json(
            workflow_id,
            "workflow.json",
            {
                "workflow_id": workflow_id,
                "workflow_type": str(workflow_type),
                "objective": str(objective),
                "status": "running",
                "nodes": [],
            },
        )
        return workflow_id

    def workflow_dir(self, workflow_id):
        if not SAFE_NAME_PATTERN.fullmatch(str(workflow_id)):
            raise ValueError("invalid workflow id")
        path = (self.root / str(workflow_id)).resolve()
        self._within_root(path)
        return path

    def node_dir(self, workflow_id, node_id):
        if not SAFE_NAME_PATTERN.fullmatch(str(node_id)):
            raise ValueError("invalid node id")
        path = (self.workflow_dir(workflow_id) / str(node_id)).resolve()
        self._within_root(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(self, workflow_id, node_id, artifact, content):
        path = self._artifact_path(workflow_id, node_id, artifact)
        text = str(content)
        path.write_text(text, encoding="utf-8")
        return {
            "artifact_ref": f"{node_id}/{artifact}",
            "chars": len(text),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def write_node_json(self, workflow_id, node_id, artifact, payload):
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        return self.write_text(workflow_id, node_id, artifact, text)

    def write_json(self, workflow_id, artifact, payload):
        if not SAFE_NAME_PATTERN.fullmatch(str(artifact)):
            raise ValueError("invalid artifact name")
        path = (self.workflow_dir(workflow_id) / artifact).resolve()
        self._within_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            temp_path = Path(handle.name)
        temp_path.replace(path)
        return path

    def read_text(self, workflow_id, node_id, artifact, *, start_line=1, end_line=200, max_chars=4000):
        path = self._artifact_path(workflow_id, node_id, artifact, create_parent=False)
        if not path.is_file():
            raise ValueError("workflow artifact does not exist")
        start_line, end_line, max_chars = int(start_line), int(end_line), int(max_chars)
        if start_line < 1 or end_line < start_line or max_chars < 1 or max_chars > 4000:
            raise ValueError("invalid artifact range")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        body = "\n".join(
            f"{number:>4}: {line}"
            for number, line in enumerate(lines[start_line - 1 : end_line], start=start_line)
        )
        truncated = len(body) > max_chars
        if truncated:
            body = body[: max(0, max_chars - 3)] + "..."
        return {
            "artifact_ref": f"{node_id}/{artifact}",
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "content": body,
            "truncated": truncated,
        }

    def _artifact_path(self, workflow_id, node_id, artifact, *, create_parent=True):
        if not SAFE_NAME_PATTERN.fullmatch(str(artifact)):
            raise ValueError("invalid artifact name")
        node_dir = self.node_dir(workflow_id, node_id) if create_parent else self._existing_node_dir(workflow_id, node_id)
        path = (node_dir / str(artifact)).resolve()
        self._within_root(path)
        return path

    def _existing_node_dir(self, workflow_id, node_id):
        if not SAFE_NAME_PATTERN.fullmatch(str(node_id)):
            raise ValueError("invalid node id")
        path = (self.workflow_dir(workflow_id) / str(node_id)).resolve()
        self._within_root(path)
        return path

    def _within_root(self, path):
        try:
            path.relative_to(self.root)
        except ValueError:
            raise ValueError("workflow path escapes artifact root") from None

