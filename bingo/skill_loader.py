"""Lazy Skill instruction and declared-resource loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_SKILL_FILE_CHARS = 16384
MAX_SKILL_BODY_CHARS = 8000
MAX_RESOURCE_FILE_CHARS = 65536
MAX_RESOURCE_READ_CHARS = 4000


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    version: str
    body: str
    content_hash: str
    allowed_tools: tuple[str, ...]
    resources: tuple[str, ...]


def _split_body(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("invalid Skill frontmatter")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    raise ValueError("invalid Skill frontmatter")


class SkillLoader:
    def __init__(self, registry, session_state, *, max_body_chars=MAX_SKILL_BODY_CHARS):
        self.registry = registry
        self.session_state = session_state
        self.session_state.setdefault("loaded", {})
        self.session_state.setdefault("active", [])
        self.max_body_chars = int(max_body_chars)
        self._cache = {}

    def load(self, name):
        metadata = self.registry.skills.get(str(name))
        if metadata is None:
            raise KeyError(f"unknown skill: {name}")
        raw = metadata.skill_file.read_text(encoding="utf-8", errors="strict")
        if len(raw) > MAX_SKILL_FILE_CHARS:
            raise ValueError("Skill file is too large")
        body = _split_body(raw)
        if not body:
            raise ValueError("Skill body is empty")
        if len(body) > self.max_body_chars:
            raise ValueError("Skill body exceeds the context limit")
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        cached = self._cache.get(metadata.name)
        if cached is not None and cached.content_hash == content_hash:
            return cached
        loaded = LoadedSkill(
            name=metadata.name,
            version=metadata.version,
            body=body,
            content_hash=content_hash,
            allowed_tools=metadata.allowed_tools,
            resources=metadata.resources,
        )
        self._cache[metadata.name] = loaded
        previous = self.session_state["loaded"].get(metadata.name, {})
        previous_resources = (
            previous.get("loaded_resources", {})
            if previous.get("content_hash") == content_hash
            else {}
        )
        self.session_state["loaded"][metadata.name] = {
            "version": metadata.version,
            "content_hash": content_hash,
            "metadata_hash": metadata.metadata_hash,
            "loaded_resources": dict(previous_resources),
        }
        return loaded

    def read_resource(
        self, skill_name, resource_path, *, max_chars=MAX_RESOURCE_READ_CHARS
    ):
        loaded = self.load(skill_name)
        value = str(resource_path).strip().replace("\\", "/")
        normalized = PurePosixPath(value).as_posix()
        if normalized not in loaded.resources:
            raise ValueError("resource is not declared by the Skill")
        if not 1 <= int(max_chars) <= MAX_RESOURCE_READ_CHARS:
            raise ValueError(f"max_chars must be in [1,{MAX_RESOURCE_READ_CHARS}]")
        metadata = self.registry.skills[loaded.name]
        target = (metadata.skill_dir / Path(normalized)).resolve()
        try:
            target.relative_to(metadata.skill_dir)
        except ValueError:
            raise ValueError("resource path escapes the Skill directory") from None
        if not target.is_file():
            raise ValueError("declared Skill resource does not exist")
        raw = target.read_text(encoding="utf-8", errors="strict")
        if len(raw) > MAX_RESOURCE_FILE_CHARS:
            raise ValueError("Skill resource is too large")
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        limit = int(max_chars)
        content = raw if len(raw) <= limit else raw[: max(0, limit - 3)] + "..."
        self.session_state["loaded"][loaded.name]["loaded_resources"][normalized] = (
            content_hash
        )
        return {
            "skill_name": loaded.name,
            "path": normalized,
            "content": content,
            "content_hash": content_hash,
            "truncated": len(content) < len(raw),
        }
