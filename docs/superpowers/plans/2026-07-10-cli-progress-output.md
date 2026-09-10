# CLI Progress Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional `--progress` terminal output that shows live model attempts, tool starts/results, concise failures, recovery events, and final stop state.

**Architecture:** `Bingo.emit_trace()` remains the event source and persists each redacted event before notifying an optional sink. A dependency-free `ConsoleProgressRenderer` formats selected events to stderr; the CLI injects it only when `--progress` is supplied.

**Tech Stack:** Python standard library, Bingo runtime events, argparse, pytest.

---

## File Structure

- Create `bingo/progress.py`: concise event rendering and clipping.
- Modify `bingo/runtime.py`: optional event sink plus pre-execution `tool_started` event.
- Modify `bingo/cli.py`: `--progress` parsing and renderer injection for new/resumed sessions.
- Create `tests/test_progress.py`: renderer, runtime event ordering, redaction, and failure isolation.
- Modify `tests/test_bingo.py`: parser and `build_agent()` wiring assertions.

### Task 1: Console Renderer

**Files:**
- Create: `tests/test_progress.py`
- Create: `bingo/progress.py`

- [ ] **Step 1: Write failing renderer tests**

Create a `StringIO`, call `ConsoleProgressRenderer(stream=stream, max_steps=24)` with `model_requested`, `tool_started`, `tool_executed`, recovery checkpoint, and `run_finished` event dictionaries. Assert concise counters, allowlisted arguments, error codes, clipped single-line results, hidden routine checkpoints, and completion text.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_progress.py -v`

Expected: collection fails because `bingo.progress` does not exist.

- [ ] **Step 3: Implement the renderer**

Implement:

```python
class ConsoleProgressRenderer:
    def __init__(self, stream=None, max_steps=6):
        self.stream = stream or sys.stderr
        self.max_steps = int(max_steps)
        self.attempt = 0

    def __call__(self, event):
        lines = self.render(event)
        for line in lines:
            print(line, file=self.stream, flush=True)
```

Use pure helpers to normalize whitespace, clip values, format allowlisted tool arguments, summarize shell results, and map tool statuses. Ignore unknown events.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_progress.py -v`

Expected: renderer tests pass.

### Task 2: Runtime Event Sink and Tool Start

**Files:**
- Modify: `tests/test_progress.py`
- Modify: `bingo/runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Construct a real `Bingo` with `FakeModelClient`, `event_sink=events.append`, and a read tool response. Assert `tool_started` occurs before `tool_executed`, the sink receives redacted payloads, and trace events remain persisted. Add a sink that raises and assert `ask()` still returns its final answer.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_progress.py -v -k "runtime or sink or tool_started"`

Expected: `Bingo.__init__()` rejects `event_sink` or no `tool_started` event appears.

- [ ] **Step 3: Implement runtime integration**

Add `event_sink=None` to `Bingo.__init__()`, assign it to `self.event_sink`, and update `emit_trace()`:

```python
self.run_store.append_trace(task_state, payload)
if self.event_sink is not None:
    try:
        self.event_sink(dict(payload))
    except Exception:
        pass
```

Emit `tool_started` with `name` and `args` after `task_state.record_tool(name)` and immediately before `run_tool(name, args)`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_progress.py -v`

Expected: runtime ordering, redaction, and sink isolation tests pass.

### Task 3: CLI Flag and Wiring

**Files:**
- Modify: `tests/test_bingo.py`
- Modify: `bingo/cli.py`

- [ ] **Step 1: Write failing CLI tests**

Assert parser default `args.progress is False`, explicit `--progress` sets it true, and `build_agent()` supplies a callable event sink only when enabled. Cover both a new session and `--resume latest` using the existing temporary workspace/session helpers.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_bingo.py -v -k "progress"`

Expected: parser has no `progress` attribute or build wiring assertion fails.

- [ ] **Step 3: Implement CLI wiring**

Import `ConsoleProgressRenderer`, add:

```python
parser.add_argument(
    "--progress",
    action="store_true",
    help="Show concise live progress on stderr.",
)
```

In `build_agent()`, create `event_sink = ConsoleProgressRenderer(max_steps=args.max_steps) if args.progress else None` and pass it to both `Bingo(...)` and `Bingo.from_session(...)`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_bingo.py -v -k "progress"`

Expected: CLI progress tests pass.

### Task 4: Verification

**Files:**
- Verify: `bingo/progress.py`
- Verify: `bingo/runtime.py`
- Verify: `bingo/cli.py`
- Verify: `tests/test_progress.py`
- Verify: `tests/test_bingo.py`

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_progress.py tests/test_bingo.py -q`

Expected: zero failures.

- [ ] **Step 2: Run complete tests**

Run: `python -m pytest tests -q`

Expected: zero failures.

- [ ] **Step 3: Run lint when available**

Run: `python -m ruff check bingo/progress.py bingo/runtime.py bingo/cli.py tests/test_progress.py tests/test_bingo.py`

Expected: zero lint errors; if Ruff is unavailable, report that explicitly without installing dependencies.

- [ ] **Step 4: Demonstrate output**

Run a deterministic `FakeModelClient` example with `ConsoleProgressRenderer` and confirm progress appears before tool completion, error summaries are clipped, and the final answer remains separate.

## Execution Note

This workspace does not expose a valid Git repository, so commit steps are omitted. Side-conversation rules prohibit subagents, so execute inline with `executing-plans`.
