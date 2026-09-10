# Bingo Multi-turn Evaluation Design

## Goal

Add deterministic tests and an evaluator for two metrics:

1. `conversation_completion_rate`: the proportion of conversations whose required turns pass and whose final workspace verifier passes.
2. `context_hit_rate`: the proportion of required context targets that appear in the rendered relevant-memory section of the prompt on the dependent turn.

The evaluator must exercise Bingo's real session, history, context manager, trace, and run-store behavior. It must not treat `TaskState.status == "completed"` as proof that the user task was completed.

## Scope

Create these files:

- `bingo/conversation_evaluator.py`
- `benchmarks/multi_turn_tasks.json`
- `tests/test_conversation_evaluator.py`

No runtime changes are required for the first version. Existing single-turn benchmark behavior remains unchanged.

## Conversation Model

One benchmark task creates one workspace copy and one `Bingo` instance. Every turn calls `agent.ask()` on that same instance. Each call creates a separate run artifact, while the session history and layered memory continue across turns.

Each benchmark task contains:

- `id`
- `fixture_repo`
- `allowed_tools`
- `total_step_budget`
- `turns`
- `final_verifier`

Each turn contains:

- `id`
- `user`
- optional `answer_contains`
- optional `required_context`

Each required-context item contains a stable `id` and a literal `text` target. Literal matching is case-insensitive and whitespace-normalized. Semantic or model-judged matching is intentionally out of scope so the test remains deterministic.

## Tool Boundary

The evaluator restricts `agent.tools` to the task's `allowed_tools`. This makes the declared benchmark contract executable rather than descriptive.

## Turn Evaluation

A turn passes when all configured answer checks pass and its run stops with `final_answer_returned`. Turns without answer checks pass when the run stops normally.

For every turn, the evaluator reads all `prompt_built` events from that run's `trace.jsonl`. A required context target is a hit when its normalized text appears in any `rendered_notes` entry under `prompt_metadata.relevant_memory` for that turn. Looking at the trace instead of only `report.json` covers multiple model attempts within one `ask()` call.

## Conversation Completion Rate

A conversation passes when:

- all required turns pass;
- the final workspace verifier exits with code zero;
- total tool steps across turns do not exceed `total_step_budget`.

Formula:

```text
conversation_completion_rate = passed conversations / total conversations
```

An empty task list produces `0.0` rather than division by zero.

## Context Hit Rate

The denominator is the number of declared required-context targets across all dependent turns. The numerator is the number of those targets found in rendered relevant memory.

```text
context_hit_rate = rendered target hits / required context targets
```

When no context targets are declared, the aggregate rate is `0.0`. Per-turn output retains raw hit and target counts so the result can be audited.

## Output

The evaluator returns a JSON-serializable artifact containing:

- benchmark and model metadata;
- `summary.conversation_completion_rate`;
- `summary.context_hit_rate`;
- total, passed, and failed conversation counts;
- context hit and target counts;
- one row per conversation;
- one row per turn, including answer, run ID, stop reason, tool steps, context counts, and pass status.

## Error Handling

Schema validation rejects missing task IDs, empty turns, invalid budgets, unknown required-context structures, and empty tool allowlists. Verifier failures become failed benchmark rows with captured exit code, stdout, and stderr. A missing fixture is a validation error.

## Tests

Tests use `FakeModelClient` or a deterministic prompt-aware model client. They cover:

1. The same `Bingo` instance is reused across turns and the dependent turn receives earlier context.
2. A completed conversation contributes to `conversation_completion_rate`.
3. A failed final verifier makes the conversation fail even when every model turn returned `<final>`.
4. A rendered required-context target contributes to `context_hit_rate`.
5. A missing target does not count as a hit.
6. Tool allowlists remove undeclared tools.
7. Summary calculations handle empty rows and zero context targets.

## Non-goals

- LLM-as-judge scoring
- fuzzy semantic matching
- confidence intervals or repeated stochastic provider runs
- changing `TaskState` success semantics
- modifying existing `memory_hit_rate` in this change
