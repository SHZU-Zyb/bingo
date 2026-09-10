"""Project-local Skill metadata discovery.

Only the bounded frontmatter of ``.bingo/skills/*/SKILL.md`` is read during
discovery.  Skill instructions and resources stay out of memory until a route
selects them.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_FRONTMATTER_CHARS = 8192
MAX_SKILLS = 128
ALLOWED_RESOURCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    version: str
    aliases: tuple[str, ...]
    triggers: tuple[str, ...]
    priority: int
    allowed_tools: tuple[str, ...]
    resources: tuple[str, ...]
    skill_dir: Path
    skill_file: Path
    metadata_hash: str

    @property
    def routing_text(self):
        return " ".join((self.name, self.description, *self.aliases, *self.triggers))

    def card(self):
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "aliases": list(self.aliases),
            "triggers": list(self.triggers),
            "priority": self.priority,
        }


def _scalar(value):
    value = str(value).strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        try:
            parsed = ast.literal_eval(value)
            return str(parsed)
        except (ValueError, SyntaxError):
            return value[1:-1]
    return value


def _inline_list(value):
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = [part.strip() for part in value[1:-1].split(",")]
        if isinstance(parsed, (list, tuple)):
            return [_scalar(item) for item in parsed]
    return [_scalar(value)]


def parse_frontmatter(text):
    """Parse the deliberately small YAML subset accepted by Bingo Skills."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or lines[-1].strip() != "---":
        raise ValueError("invalid_frontmatter")
    data, current = {}, None
    list_fields = {"aliases", "triggers", "allowed_tools"}
    for raw in lines[1:-1]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            if ":" not in stripped:
                raise ValueError("invalid_frontmatter")
            key, value = (part.strip() for part in stripped.split(":", 1))
            current = key
            if key in list_fields:
                data[key] = _inline_list(value)
            elif key == "resources":
                data[key] = []
                if value:
                    data[key].extend(_inline_list(value))
            else:
                data[key] = _scalar(value)
            continue
        if current in list_fields and stripped.startswith("-"):
            data[current].append(_scalar(stripped[1:]))
            continue
        if current == "resources":
            if stripped.startswith("-"):
                data["resources"].append(_scalar(stripped[1:]))
            elif stripped.endswith(":"):
                continue
            else:
                raise ValueError("invalid_frontmatter")
            continue
        raise ValueError("invalid_frontmatter")
    return data


def _read_bounded_frontmatter(path, limit):
    chars, lines = 0, []
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        first = handle.readline()
        if first.strip() != "---":
            raise ValueError("invalid_frontmatter")
        chars += len(first)
        lines.append(first.rstrip("\r\n"))
        for line in handle:
            chars += len(line)
            if chars > limit:
                raise ValueError("frontmatter_too_large")
            lines.append(line.rstrip("\r\n"))
            if line.strip() == "---":
                return "\n".join(lines)
    raise ValueError("invalid_frontmatter")


def _validated_strings(values, field, limit=32):
    result = []
    for raw in values or []:
        value = str(raw).strip()
        if not value or len(value) > 128:
            raise ValueError(f"invalid_{field}")
        if value not in result:
            result.append(value)
        if len(result) > limit:
            raise ValueError(f"too_many_{field}")
    return tuple(result)


def _validate_resource(raw):
    value = str(raw).strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() not in ALLOWED_RESOURCE_SUFFIXES
    ):
        raise ValueError("invalid_resource")
    return path.as_posix()


class SkillRegistry:
    def __init__(
        self,
        workspace_root,
        *,
        max_frontmatter_chars=MAX_FRONTMATTER_CHARS,
        max_skills=MAX_SKILLS,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.skills_root = self.workspace_root / ".bingo" / "skills"
        self.max_frontmatter_chars = int(max_frontmatter_chars)
        self.max_skills = int(max_skills)
        self.skills = {}
        self.diagnostics = []
        self.fingerprint = hashlib.sha256(b"empty-skill-registry").hexdigest()

    def _diagnose(self, code, path, message=""):
        self.diagnostics.append(
            {"code": code, "path": str(path), "message": message or code}
        )

    def discover(self):
        self.skills, self.diagnostics = {}, []
        if not self.skills_root.is_dir():
            return self._finish()
        try:
            self.skills_root.resolve().relative_to(self.workspace_root)
        except ValueError:
            self._diagnose("skill_root_escape", self.skills_root)
            return self._finish()
        files = sorted(
            self.skills_root.glob("*/SKILL.md"),
            key=lambda item: item.as_posix().lower(),
        )
        if len(files) > self.max_skills:
            self._diagnose("too_many_skills", self.skills_root)
            files = files[: self.max_skills]
        duplicates = set()
        resolved_skills_root = self.skills_root.resolve()
        for skill_file in files:
            try:
                try:
                    skill_file.resolve().relative_to(resolved_skills_root)
                except ValueError:
                    raise ValueError("skill_path_escape") from None
                frontmatter = _read_bounded_frontmatter(
                    skill_file, self.max_frontmatter_chars
                )
                raw = parse_frontmatter(frontmatter)
                metadata = self._metadata(skill_file, raw)
            except (OSError, UnicodeError, ValueError) as exc:
                code = str(exc) if str(exc) else "invalid_metadata"
                self._diagnose(code, skill_file)
                continue
            if metadata.name in self.skills:
                duplicates.add(metadata.name)
                del self.skills[metadata.name]
                self._diagnose("duplicate_name", skill_file, metadata.name)
                continue
            if metadata.name in duplicates:
                self._diagnose("duplicate_name", skill_file, metadata.name)
                continue
            self.skills[metadata.name] = metadata
        return self._finish()

    def _metadata(self, skill_file, raw):
        name = str(raw.get("name", "")).strip().lower()
        description = str(raw.get("description", "")).strip()
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise ValueError("invalid_name")
        if not description or len(description) > 500:
            raise ValueError("invalid_description")
        aliases = _validated_strings(raw.get("aliases", []), "aliases")
        triggers = _validated_strings(raw.get("triggers", []), "triggers", limit=64)
        allowed_tools = _validated_strings(
            raw.get("allowed_tools", []), "allowed_tools", limit=64
        )
        resources = tuple(
            dict.fromkeys(_validate_resource(item) for item in raw.get("resources", []))
        )
        try:
            priority = int(raw.get("priority", 50) or 50)
        except (TypeError, ValueError):
            raise ValueError("invalid_priority") from None
        if not 0 <= priority <= 100:
            raise ValueError("invalid_priority")
        version = str(raw.get("version", "1")).strip() or "1"
        payload = {
            "name": name,
            "description": description,
            "version": version,
            "aliases": aliases,
            "triggers": triggers,
            "priority": priority,
            "allowed_tools": allowed_tools,
            "resources": resources,
        }
        metadata_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return SkillMetadata(
            **payload,
            skill_dir=skill_file.parent.resolve(),
            skill_file=skill_file.resolve(),
            metadata_hash=metadata_hash,
        )

    def _finish(self):
        self.skills = dict(sorted(self.skills.items()))
        payload = [(name, item.metadata_hash) for name, item in self.skills.items()]
        self.fingerprint = hashlib.sha256(
            json.dumps(payload).encode("utf-8")
        ).hexdigest()
        return self

    def cards(self):
        return [metadata.card() for metadata in self.skills.values()]
