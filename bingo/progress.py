"""Concise terminal rendering for Bingo runtime events."""

import sys


RECOVERY_TRIGGERS = {
    "context_reduction",
    "freshness_mismatch",
    "workspace_mismatch",
}


def _single_line(value):
    return " ".join(str(value or "").split())


def _clip(value, limit):
    text = _single_line(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _tool_details(name, args):
    args = dict(args or {})
    if name == "read_file":
        path = _clip(args.get("path", "."), 100)
        start = int(args.get("start", 1) or 1)
        end = int(args.get("end", 200) or 200)
        return f"path={path} lines={start}-{end}"
    if name in {"write_file", "patch_file", "list_files"}:
        path = _clip(args.get("path", "."), 100)
        return f"path={path}"
    if name == "search":
        pattern = _clip(args.get("pattern", ""), 70)
        path = _clip(args.get("path", "."), 70)
        return f'pattern="{pattern}" path={path}'
    if name == "run_shell":
        command = _clip(args.get("command", ""), 100)
        return f'command="{command}"'
    if name == "delegate":
        task = _clip(args.get("task", ""), 100)
        return f'task="{task}"'
    return ""


def _duration_ms(event):
    duration = int(event.get("duration_ms", 0) or 0)
    return f" {duration}ms" if duration else ""


class ConsoleProgressRenderer:
    def __init__(self, stream=None, max_steps=12):
        self.stream = stream if stream is not None else sys.stderr
        self.max_steps = int(max_steps)
        self.attempt = 0

    def __call__(self, event):
        for line in self.render(dict(event or {})):
            print(line, file=self.stream, flush=True)

    def marker(self):
        attempt = self.attempt or 1
        return f"[{attempt}/{self.max_steps}]"

    def render(self, event):
        event_name = str(event.get("event", ""))
        if event_name == "run_started":
            return ["[run] started"]

        if event_name == "model_requested":
            self.attempt = int(event.get("attempts", 0) or 0)
            return [f"{self.marker()} thinking..."]

        if event_name == "model_parsed" and event.get("kind") == "retry":
            return [f"{self.marker()} model output invalid; retrying"]

        if event_name == "tool_started":
            name = str(event.get("name", "unknown"))
            details = _tool_details(name, event.get("args", {}))
            suffix = f" {details}" if details else ""
            return [f"{self.marker()} tool {name}{suffix}"]

        if event_name == "tool_executed":
            status = str(event.get("tool_status", "ok") or "ok")
            duration = _duration_ms(event)
            if status == "ok":
                if event.get("read_cache_action") == "trimmed":
                    skipped = _single_line(event.get("skipped_cached_range", ""))
                    effective_args = dict(event.get("effective_args", {}) or {})
                    start = int(effective_args.get("start", 1) or 1)
                    end = int(effective_args.get("end", 200) or 200)
                    return [f"{self.marker()} ok{duration} cached={skipped} read={start}-{end}"]
                return [f"{self.marker()} ok{duration}"]
            display_status = "partial" if status == "partial_success" else status
            error_code = str(event.get("tool_error_code", "")).strip()
            code = f" {error_code}" if error_code else ""
            result = _clip(event.get("result", ""), 140)
            summary = f": {result}" if result else ""
            return [f"{self.marker()} {display_status}{code}{duration}{summary}"]

        if event_name == "checkpoint_created":
            trigger = str(event.get("trigger", ""))
            if trigger in RECOVERY_TRIGGERS:
                return [f"[run] recovery {trigger}"]
            return []

        if event_name == "runtime_identity_mismatch":
            fields = ", ".join(str(field) for field in event.get("fields", [])) or "unknown"
            return [f"[run] runtime mismatch: {fields}"]

        if event_name == "run_finished":
            status = str(event.get("status", "finished"))
            stop_reason = str(event.get("stop_reason", "")).strip()
            duration_ms = int(event.get("run_duration_ms", 0) or 0)
            duration = f" {duration_ms / 1000:.2f}s" if duration_ms else ""
            reason = f" {stop_reason}" if stop_reason else ""
            return [f"[run] {status}{reason}{duration}"]

        return []
