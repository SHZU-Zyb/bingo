# Bingo CLI Progress Output Design

## Goal

Add optional concise, live terminal progress for Bingo runs so a user can see model attempts, tool calls, failures, recovery events, and completion without opening `trace.jsonl`.

Progress is disabled by default and enabled with `--progress`.

## User Experience

Example invocation:

```powershell
python -m bingo --provider deepseek --progress "Run the failing test and fix it."
```

Example output:

```text
[run] started
[1/24] thinking...
[1/24] tool read_file path=tests/test_demo.py
[1/24] ok 15ms
[2/24] thinking...
[2/24] tool run_shell command="python -m pytest tests/test_demo.py -v"
[2/24] error exit_code=1: AssertionError
[3/24] tool patch_file path=bingo/runtime.py
[3/24] ok changed=bingo/runtime.py
[run] completed final_answer_returned 3.2s
```

Progress lines are written to `stderr`. The welcome screen and final answer retain their existing output behavior on `stdout`, preserving one-shot output for callers that capture standard output.

## Architecture

Bingo already emits structured, redacted runtime events through `emit_trace()`. The implementation extends `Bingo` with an optional `event_sink` callable. After an event has been redacted and appended to `trace.jsonl`, `emit_trace()` sends the same event dictionary to the sink.

The CLI creates a `ConsoleProgressRenderer` only when `--progress` is present and passes it into `Bingo` or `Bingo.from_session()`. Without the flag, `event_sink` is `None` and runtime behavior is unchanged.

The renderer lives in `bingo/progress.py`. It owns presentation only and has no knowledge of model clients, session persistence, or tool execution internals.

## Events

The renderer handles these events:

- `run_started`: print a short run-start line.
- `model_requested`: record the current attempt and tool-step counters; print `thinking...`.
- `model_parsed`: print a retry warning only when the parsed kind is `retry`; normal tool/final parsing stays quiet.
- `tool_started`: emitted immediately before execution; print tool name plus a small allowlisted argument summary so long-running shell commands remain observable.
- `tool_executed`: print status, duration, and a short problem/result summary after execution.
- `runtime_identity_mismatch`: print the mismatched fields.
- `checkpoint_created`: print only recovery-related triggers such as `freshness_mismatch`, `workspace_mismatch`, and `context_reduction`; routine tool/final checkpoints stay quiet.
- `run_finished`: print status, stop reason, and duration.

## Tool Formatting

Only high-value arguments are displayed:

- File tools: `path`.
- `search`: `pattern` and `path`.
- `run_shell`: `command`, clipped to 100 characters.
- `delegate`: `task`, clipped to 100 characters.

Other arguments and complete file contents are not printed.

Tool status comes from `tool_status`:

- `ok`: print `ok` and duration.
- `partial_success`: print `partial` and a clipped result summary.
- `error`: print `error`, error code, and a clipped result summary.
- `rejected`: print `rejected`, error code, and a clipped result summary.

Result summaries are single-line, whitespace-normalized, and clipped to 140 characters. For `run_shell`, the formatter prefers an `exit_code` and the first non-empty stderr/stdout detail instead of printing the complete captured output.

## Safety

The sink receives the output of `redact_artifact()`, not the original payload. It must never receive unredacted event data.

Renderer failures must not interrupt an Agent run. `emit_trace()` catches sink exceptions after the trace event has been persisted. The complete trace remains the source of truth even when terminal rendering fails.

Progress output uses `flush=True` so long-running model or shell operations remain visibly active.

## CLI Contract

Add:

```text
--progress  Show concise live progress on stderr.
```

The default remains `False`. The option applies to one-shot mode, interactive mode, and resumed sessions.

## Files

- Create `bingo/progress.py` for `ConsoleProgressRenderer` and formatting helpers.
- Modify `bingo/runtime.py` to accept and notify an optional `event_sink`.
- Modify `bingo/runtime.py` to emit `tool_started` immediately before calling `run_tool()`.
- Modify `bingo/cli.py` to parse `--progress`, construct the renderer, and pass it through new and resumed Agent creation.
- Create `tests/test_progress.py` for renderer and runtime sink tests.
- Modify CLI parser tests in `tests/test_bingo.py` only if needed to verify the new flag.

## Tests

Tests cover:

1. Progress defaults to disabled.
2. `--progress` is accepted by the parser.
3. `build_agent()` injects a renderer for new and resumed sessions when enabled.
4. Runtime sends the already-redacted event to the sink.
5. A sink exception does not fail `ask()` and the trace event remains present.
6. Model attempts show counters.
7. Successful file tools show tool name, path, status, and duration.
8. Failed shell tools show a concise exit/error summary without full output.
9. Routine checkpoints are hidden while recovery checkpoints are visible.
10. Progress goes to the supplied stream, which defaults to `sys.stderr`.
11. `tool_started` is visible before a long-running tool returns.

## Non-goals

- Streaming model tokens.
- Printing model chain-of-thought or raw model responses.
- Replacing `trace.jsonl` or `report.json`.
- Adding colors, spinners, or third-party terminal dependencies.
- Changing step limits, approval behavior, or one-shot lifecycle.
