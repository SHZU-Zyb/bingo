"""Freshness-checked cache for source text already read by tools.

The cache is execution state, not semantic memory.  Entries are safe to reuse only
while the file hash and requested source range still match.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .memory import canonicalize_path, resolve_workspace_path
from .workspace import now

DEFAULT_MAX_ENTRIES = 8
DEFAULT_MAX_CONTENT_CHARS = 4000


def default_evidence_cache_state():
    return {"entries": [], "next_access_index": 0}


def _file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def normalize_evidence_cache_state(state, workspace_root=None):
    if not isinstance(state, dict):
        state = default_evidence_cache_state()
    entries = state.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    normalized = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = canonicalize_path(item.get("path", ""), workspace_root).strip()
        content = str(item.get("content", ""))
        try:
            start_line = int(item.get("start_line", 0))
            end_line = int(item.get("end_line", 0))
            access_index = int(item.get("access_index", 0))
        except (TypeError, ValueError):
            continue
        if not path or not content or start_line < 1 or end_line < start_line:
            continue
        normalized.append(
            {
                "cache_key": str(item.get("cache_key", "")),
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "content": content,
                "summary": str(item.get("summary", "")).strip(),
                "file_hash": str(item.get("file_hash", "")).strip(),
                "symbol_id": str(item.get("symbol_id", "")).strip(),
                "complete": bool(item.get("complete", True)),
                "created_at": str(item.get("created_at", "")).strip() or now(),
                "last_accessed_at": str(item.get("last_accessed_at", "")).strip()
                or now(),
                "access_index": max(0, access_index),
            }
        )
    state["entries"] = normalized
    try:
        next_index = int(state.get("next_access_index", 0))
    except (TypeError, ValueError):
        next_index = 0
    state["next_access_index"] = max(
        next_index, max((e["access_index"] for e in normalized), default=-1) + 1
    )
    return state


class EvidenceCache:
    def __init__(
        self,
        state=None,
        workspace_root=None,
        max_entries=DEFAULT_MAX_ENTRIES,
        max_content_chars=DEFAULT_MAX_CONTENT_CHARS,
    ):
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else None
        )
        self.max_entries = max(1, int(max_entries))
        self.max_content_chars = max(256, int(max_content_chars))
        self.state = normalize_evidence_cache_state(state, self.workspace_root)
        for entry in self.state["entries"]:
            if len(entry["content"]) > self.max_content_chars:
                entry["content"] = entry["content"][: self.max_content_chars]
                entry["complete"] = False
        self.state["entries"] = sorted(
            self.state["entries"], key=lambda item: item["access_index"]
        )[-self.max_entries :]
        self.invalidate_stale()

    def to_dict(self):
        self.state = normalize_evidence_cache_state(self.state, self.workspace_root)
        return self.state

    def canonical_path(self, path):
        return canonicalize_path(path, self.workspace_root)

    def _touch(self, entry):
        entry["last_accessed_at"] = now()
        entry["access_index"] = self.state["next_access_index"]
        self.state["next_access_index"] += 1

    def _current_hash(self, path):
        resolved = resolve_workspace_path(path, self.workspace_root)
        if resolved is None or not resolved.is_file():
            return None
        return _file_hash(resolved)

    def store(
        self,
        path,
        start_line,
        end_line,
        content,
        summary="",
        symbol_id="",
        file_hash=None,
        complete=True,
    ):
        canonical = canonicalize_path(path, self.workspace_root).strip()
        resolved = resolve_workspace_path(canonical, self.workspace_root)
        start_line, end_line = int(start_line), int(end_line)
        raw_content = str(content)
        if (
            resolved is None
            or not resolved.is_file()
            or start_line < 1
            or end_line < start_line
            or not raw_content
        ):
            return None
        current_hash = _file_hash(resolved)
        if not current_hash or (file_hash and str(file_hash) != current_hash):
            return None
        is_complete = bool(complete) and len(raw_content) <= self.max_content_chars
        stored_content = raw_content[: self.max_content_chars]
        cache_key = hashlib.sha256(
            f"{canonical}\0{start_line}\0{end_line}\0{current_hash}\0{symbol_id}".encode()
        ).hexdigest()
        entry = {
            "cache_key": cache_key,
            "path": canonical,
            "start_line": start_line,
            "end_line": end_line,
            "content": stored_content,
            "summary": str(summary).strip(),
            "file_hash": current_hash,
            "symbol_id": str(symbol_id).strip(),
            "complete": is_complete,
            "created_at": now(),
            "last_accessed_at": now(),
            "access_index": 0,
        }
        self.state["entries"] = [
            item for item in self.state["entries"] if item["cache_key"] != cache_key
        ]
        self._touch(entry)
        self.state["entries"].append(entry)
        self.state["entries"] = sorted(
            self.state["entries"], key=lambda item: item["access_index"]
        )[-self.max_entries :]
        return dict(entry)

    def invalidate_path(self, path):
        canonical = canonicalize_path(path, self.workspace_root).strip()
        before = len(self.state["entries"])
        self.state["entries"] = [
            item for item in self.state["entries"] if item["path"] != canonical
        ]
        return before - len(self.state["entries"])

    def invalidate_stale(self):
        retained = []
        invalidated = []
        for entry in self.state["entries"]:
            if (
                entry["file_hash"]
                and self._current_hash(entry["path"]) == entry["file_hash"]
            ):
                retained.append(entry)
            else:
                invalidated.append(entry["path"])
        self.state["entries"] = retained
        return invalidated

    def lookup(self, path, start_line, end_line, symbol_id=""):
        canonical = canonicalize_path(path, self.workspace_root).strip()
        self.invalidate_stale()
        matches = [
            entry
            for entry in self.state["entries"]
            if entry["path"] == canonical
            and entry["complete"]
            and entry["start_line"] <= int(start_line)
            and entry["end_line"] >= int(end_line)
            and (
                not symbol_id
                or not entry["symbol_id"]
                or entry["symbol_id"] == symbol_id
            )
        ]
        if not matches:
            return None
        entry = max(matches, key=lambda item: item["access_index"])
        self._touch(entry)
        return dict(entry)

    def match_sources(self, sources, limit=3):
        matches, seen = [], set()
        for source in sources:
            match = self.lookup(
                source.get("path", ""),
                source.get("start_line", 0),
                source.get("end_line", 0),
                source.get("symbol_id", ""),
            )
            if not match:
                continue
            expected_hash = str(source.get("content_hash", "")).strip()
            if expected_hash and match["file_hash"] != expected_hash:
                continue
            if match["cache_key"] in seen:
                continue
            seen.add(match["cache_key"])
            matches.append(match)
            if len(matches) >= int(limit):
                break
        return matches
