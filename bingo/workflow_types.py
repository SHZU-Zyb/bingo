"""Serializable records shared by local and Agent workflow nodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class FailureRecord:
    test_id: str
    message: str = ""
    exception: str = ""
    source_refs: tuple[str, ...] = ()

    def to_dict(self):
        data = asdict(self)
        data["source_refs"] = list(self.source_refs)
        return data


@dataclass
class WorkflowNode:
    node_id: str
    kind: str
    task: str
    scope: tuple[str, ...] = ()
    status: str = "pending"
    artifact_refs: list[str] = field(default_factory=list)

    def to_dict(self):
        data = asdict(self)
        data["scope"] = list(self.scope)
        return data


@dataclass
class WorkflowRecord:
    workflow_id: str
    workflow_type: str
    objective: str
    status: str = "pending"
    nodes: list[WorkflowNode] = field(default_factory=list)

    def to_dict(self):
        return {
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "objective": self.objective,
            "status": self.status,
            "nodes": [node.to_dict() for node in self.nodes],
        }

