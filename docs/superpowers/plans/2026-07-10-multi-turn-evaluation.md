# Multi-turn Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic multi-turn benchmark support for `conversation_completion_rate` and `context_hit_rate`.

**Architecture:** A new evaluator creates one fixture copy and one `Bingo` instance per conversation, then calls `ask()` for every turn on that same instance. It reads `prompt_built` trace events to score rendered context targets and runs a final workspace verifier before counting a conversation as complete.

**Tech Stack:** Python 3.10+, standard library, Bingo runtime, pytest.

---

## File Structure

- Create `bingo/conversation_evaluator.py`: schema validation, task execution, trace scoring, verifier execution, metric aggregation, artifact writing.
- Create `benchmarks/multi_turn_tasks.json`: deterministic multi-turn fixture task and scripted model outputs.
- Create `tests/test_conversation_evaluator.py`: unit and end-to-end tests for both metrics and tool restrictions.
- Use the existing `bingo/runtime.py`, `bingo/context_manager.py`, `bingo/run_store.py`, and fixture repositories without modifying them.

### Task 1: Metric Aggregation

**Files:**
- Create: `tests/test_conversation_evaluator.py`
- Create: `bingo/conversation_evaluator.py`

- [ ] **Step 1: Write failing summary tests**

```python
from bingo.conversation_evaluator import summarize_conversation_rows


def test_summarize_conversation_rows_calculates_both_rates():
    summary = summarize_conversation_rows([
        {"passed": True, "context_hits": 2, "context_targets": 2},
        {"passed": False, "context_hits": 0, "context_targets": 1},
    ])
    assert summary["conversation_completion_rate"] == 0.5
    assert summary["context_hit_rate"] == 2 / 3


def test_summarize_conversation_rows_handles_empty_denominators():
    summary = summarize_conversation_rows([])
    assert summary["conversation_completion_rate"] == 0.0
    assert summary["context_hit_rate"] == 0.0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_conversation_evaluator.py -v`

Expected: collection fails because `bingo.conversation_evaluator` does not exist.

- [ ] **Step 3: Add the minimal aggregation implementation**

```python
def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def summarize_conversation_rows(rows):
    rows = list(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    context_hits = sum(int(row.get("context_hits", 0)) for row in rows)
    context_targets = sum(int(row.get("context_targets", 0)) for row in rows)
    return {
        "total_conversations": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "context_hits": context_hits,
        "context_targets": context_targets,
        "conversation_completion_rate": _safe_ratio(passed, len(rows)),
        "context_hit_rate": _safe_ratio(context_hits, context_targets),
    }
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_conversation_evaluator.py -v`

Expected: both summary tests pass.

### Task 2: Schema and Benchmark Data

**Files:**
- Modify: `tests/test_conversation_evaluator.py`
- Modify: `bingo/conversation_evaluator.py`
- Create: `benchmarks/multi_turn_tasks.json`

- [ ] **Step 1: Add failing schema tests**

Test that `load_multi_turn_benchmark()` accepts the checked-in task, rejects empty turns, rejects a non-positive total budget, rejects an empty tool allowlist, and rejects malformed required-context items.

- [ ] **Step 2: Run the schema tests and verify RED**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "load or reject"`

Expected: failures report that `load_multi_turn_benchmark` is missing.

- [ ] **Step 3: Add schema validation and deterministic task data**

The checked-in task reads `sample.txt` on turn one and asks for the remembered `alpha | beta | gamma` summary on turn two. Its scripted outputs are:

```json
[
  "<tool>{\"name\":\"read_file\",\"args\":{\"path\":\"sample.txt\",\"start\":1,\"end\":4}}</tool>",
  "<final>Remembered.</final>",
  "<final>The remembered sequence is alpha, beta, gamma.</final>"
]
```

The dependent turn declares `alpha | beta | gamma` as required context, and the final verifier confirms that the fixture remains readable.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "load or reject"`

Expected: all selected schema tests pass.

### Task 3: End-to-end Conversation Execution

**Files:**
- Modify: `tests/test_conversation_evaluator.py`
- Modify: `bingo/conversation_evaluator.py`

- [ ] **Step 1: Add a failing end-to-end completion test**

Run the checked-in benchmark into `tmp_path`. Assert that the artifact is written, the conversation passes, the two turns have distinct run IDs, one session ID is shared, and `conversation_completion_rate == 1.0`.

- [ ] **Step 2: Run the completion test and verify RED**

Run: `python -m pytest tests/test_conversation_evaluator.py::test_multi_turn_benchmark_calculates_completion_rate -v`

Expected: failure reports that `MultiTurnBenchmarkEvaluator` or `run()` is missing.

- [ ] **Step 3: Implement one-agent multi-turn execution**

Implement `MultiTurnBenchmarkEvaluator.run()` and `run_task()` to copy the fixture, construct one `Bingo`, restrict and rebuild its tool prefix, call `ask()` for each turn, load each run report and trace, sum tool steps, run the portable final verifier, and write the JSON artifact.

- [ ] **Step 4: Run the completion test and verify GREEN**

Run: `python -m pytest tests/test_conversation_evaluator.py::test_multi_turn_benchmark_calculates_completion_rate -v`

Expected: the test passes.

### Task 4: Context Hit Scoring

**Files:**
- Modify: `tests/test_conversation_evaluator.py`
- Modify: `bingo/conversation_evaluator.py`

- [ ] **Step 1: Add failing hit and miss tests**

Assert that the checked-in target appears in a dependent turn's `rendered_notes`, producing one hit from one target. Run a second task with a target text that is not present and assert zero hits from one target.

- [ ] **Step 2: Run context tests and verify RED**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "context_hit"`

Expected: assertions fail because context target scoring is absent.

- [ ] **Step 3: Implement trace-based target matching**

Normalize case and whitespace. Read every JSON line in a run trace, select `prompt_built` events, collect `prompt_metadata.relevant_memory.rendered_notes`, and count each declared target at most once per turn when it appears in any rendered note.

- [ ] **Step 4: Run context tests and verify GREEN**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "context_hit"`

Expected: both hit and miss tests pass.

### Task 5: Completion Guard and Tool Allowlist

**Files:**
- Modify: `tests/test_conversation_evaluator.py`
- Modify: `bingo/conversation_evaluator.py`

- [ ] **Step 1: Add failing verifier and allowlist tests**

Create an in-memory task variant whose verifier exits nonzero and assert that `<final>` answers do not make the conversation pass. Create another task whose scripted model calls a tool excluded from `allowed_tools`; assert that the tool trace records `unknown_tool` and the workspace is unchanged.

- [ ] **Step 2: Run guard tests and verify RED**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "verifier or allowlist"`

Expected: failures show verifier or tool restriction data is not enforced or reported.

- [ ] **Step 3: Complete verifier and tool-event reporting**

Require all turn checks, a zero verifier exit code, and total tool steps within budget for conversation completion. Return verifier output and per-turn tool names/error codes in auditable rows.

- [ ] **Step 4: Run guard tests and verify GREEN**

Run: `python -m pytest tests/test_conversation_evaluator.py -v -k "verifier or allowlist"`

Expected: both guard tests pass.

### Task 6: Final Verification

**Files:**
- Verify: `bingo/conversation_evaluator.py`
- Verify: `benchmarks/multi_turn_tasks.json`
- Verify: `tests/test_conversation_evaluator.py`

- [ ] **Step 1: Run the focused suite**

Run: `python -m pytest tests/test_conversation_evaluator.py -v`

Expected: all multi-turn evaluator tests pass.

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest tests -q`

Expected: zero failures.

- [ ] **Step 3: Run lint on changed Python files**

Run: `python -m ruff check bingo/conversation_evaluator.py tests/test_conversation_evaluator.py`

Expected: zero lint errors.

- [ ] **Step 4: Inspect generated artifact fields**

Run the evaluator in the end-to-end pytest test and confirm its artifact contains both summary metrics, raw numerator/denominator counts, conversation rows, turn rows, run IDs, verifier evidence, and tool-event evidence.

## Execution Note

This workspace does not expose a valid Git repository, so commit steps are intentionally omitted. Side-conversation rules prohibit subagents, so this plan must be executed inline with `executing-plans`.
