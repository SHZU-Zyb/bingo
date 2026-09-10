"""Deterministic extraction of test/build outcomes before any model reasoning."""

from __future__ import annotations

import re

from .workflow_types import FailureRecord

COUNT_PATTERNS = {
    "passed": re.compile(r"\b(\d+)\s+passed\b", re.IGNORECASE),
    "failed": re.compile(r"\b(\d+)\s+failed\b", re.IGNORECASE),
    "skipped": re.compile(r"\b(\d+)\s+skipped\b", re.IGNORECASE),
    "errors": re.compile(r"\b(\d+)\s+errors?\b", re.IGNORECASE),
}
FAILED_TEST_PATTERN = re.compile(
    r"^FAILED\s+(?P<test_id>\S+?)(?:\s+-\s+(?P<message>.+))?$",
    re.IGNORECASE | re.MULTILINE,
)
EXCEPTION_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")
SOURCE_PATTERN = re.compile(r"(?<![\w./\\-])([\w./\\-]+\.(?:py|js|ts|tsx|java|go|rs)):(\d+)")


def _last_count(pattern, text):
    matches = pattern.findall(text)
    return int(matches[-1]) if matches else 0


def _source_ref(path, line):
    return f"{str(path).replace(chr(92), '/')}:{line}"


def parse_verification_output(command, stdout, stderr, exit_code):
    """Return facts only; raw process output remains in workflow artifacts."""
    stdout, stderr = str(stdout or ""), str(stderr or "")
    combined = "\n".join(part for part in (stdout, stderr) if part)
    counts = {name: _last_count(pattern, combined) for name, pattern in COUNT_PATTERNS.items()}
    failures = []
    seen = set()
    for match in FAILED_TEST_PATTERN.finditer(combined):
        test_id = match.group("test_id").strip()
        if test_id in seen:
            continue
        seen.add(test_id)
        message = (match.group("message") or "").strip()
        exception_match = EXCEPTION_PATTERN.search(message)
        source_refs = tuple(
            dict.fromkeys(_source_ref(path, line) for path, line in SOURCE_PATTERN.findall(message))
        )
        failures.append(
            FailureRecord(
                test_id=test_id,
                message=message,
                exception=exception_match.group(1) if exception_match else "",
                source_refs=source_refs,
            ).to_dict()
        )
    if failures and counts["failed"] == 0:
        counts["failed"] = len(failures)
    source_refs = list(
        dict.fromkeys(_source_ref(path, line) for path, line in SOURCE_PATTERN.findall(combined))
    )[:20]
    return {
        "command": str(command),
        "exit_code": int(exit_code),
        "status": "passed" if int(exit_code) == 0 else "failed",
        "counts": counts,
        "failures": failures[:20],
        "source_refs": source_refs,
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
    }


def needs_diagnostic_agent(result):
    """Gate model reasoning using facts that local parsing can establish."""
    if result.get("status") == "passed":
        return {"required": False, "reasons": []}
    failures = list(result.get("failures", []))
    reasons = []
    if len(failures) > 1 or int(result.get("counts", {}).get("failed", 0)) > 1:
        reasons.append("multiple_failures")
    if not failures:
        reasons.append("unstructured_failure")
    elif any(not item.get("message") for item in failures):
        reasons.append("missing_failure_detail")
    modules = {
        str(item.get("test_id", "")).replace("\\", "/").split("/", 1)[0]
        for item in failures
        if "/" in str(item.get("test_id", "")).replace("\\", "/")
    }
    if len(modules) > 1:
        reasons.append("cross_module_failure")
    return {"required": bool(reasons), "reasons": list(dict.fromkeys(reasons))}
