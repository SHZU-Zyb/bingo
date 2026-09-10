# Adaptive Multi-Agent Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add workflow-managed child Agents only for independent parallel investigation or complex diagnosis, while keeping deterministic execution/parsing local and source editing in the parent Agent.

**Architecture:** A workflow engine provides two bounded templates: `parallel_explore` runs two to four read-only child investigations concurrently, while `verify_and_diagnose` runs a command locally, parses test output deterministically, and invokes a diagnostic child only when a reasoning gate detects ambiguity or cross-module failures. Full outputs are stored as workflow artifacts; the parent receives compact reports and evidence references.

**Tech Stack:** Python standard library (`concurrent.futures`, `subprocess`, `dataclasses`, `json`, `re`), existing Bingo runtime/tools/session/run-store, pytest.

---

### Task 1: Workflow records and deterministic result parsing

**Files:**
- Create: `bingo/workflow_types.py`
- Create: `bingo/verification_parser.py`
- Test: `tests/test_workflows.py`

- [x] Write failing tests for pytest count extraction, failure cards, clear single failures, ambiguous multi-failure diagnosis gates, and parser fallback.
- [x] Run focused tests and confirm missing workflow modules fail collection.
- [x] Implement immutable workflow/failure records and deterministic parser functions.
- [x] Run focused parser tests and confirm they pass.

### Task 2: Artifact-backed workflow store

**Files:**
- Create: `bingo/workflow_store.py`
- Modify: `tests/test_workflows.py`

- [x] Write failing tests for full stdout/stderr persistence, compact state records, bounded line reads, hashes, and traversal rejection.
- [x] Run focused tests and confirm the store behavior is missing.
- [x] Implement atomic JSON/text artifacts under the parent run directory with resolved-path containment checks.
- [x] Run focused store tests and confirm they pass.

### Task 3: Parallel exploration and serial verification engine

**Files:**
- Create: `bingo/workflow_engine.py`
- Modify: `tests/test_workflows.py`

- [x] Write failing tests proving two branches overlap in time, one branch is rejected, branch outputs remain in artifacts, local verification avoids a child for clear failures, and ambiguous failures invoke one diagnostic child.
- [x] Run focused tests and confirm the engine tests fail for missing behavior.
- [x] Implement bounded `ThreadPoolExecutor` scheduling, branch validation, deterministic verification execution/parsing, diagnostic gating, report compaction, and workflow state output.
- [x] Run focused engine tests and confirm they pass.

### Task 4: Runtime, model isolation, and tools

**Files:**
- Modify: `bingo/models.py`
- Modify: `bingo/runtime.py`
- Modify: `bingo/tools.py`
- Modify: `bingo/__init__.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_bingo.py`
- Modify: `tests/test_safety_invariants.py`

- [x] Write failing integration tests for isolated child model clients, read-only Explorer/Diagnostician tools, session workflow summaries, parent-only source writes, tool validation, and on-demand artifact reads.
- [x] Run focused integration tests and confirm expected failures.
- [x] Add model-client cloning, child capability allowlists, `parallel_workflow`, `verification_workflow`, and `read_workflow_artifact` tools.
- [x] Persist workflow state without raw logs and emit workflow metadata in trace/report.
- [x] Keep legacy `delegate` behavior compatible while routing new work through workflow tools.
- [x] Run focused integration and regression tests.

### Task 5: Skill guidance, documentation, and verification

**Files:**
- Create: `.bingo/skills/multi-agent-workflow/SKILL.md`
- Create: `.bingo/skills/multi-agent-workflow/references/routing.md`
- Create: `docs/workflows.md`
- Modify: `README.md`

- [x] Document direct-parent, local-algorithm, parallel-child, and diagnostic-child routing boundaries with examples and artifact schemas.
- [x] Add a Skill that instructs the model to prefer parent/local execution and use workflow tools only when their gates are satisfied.
- [x] Run focused workflow, runtime, context, and safety tests.
- [x] Run Ruff checks and Python compilation for changed modules.
- [x] Run `python -m pytest -q`, record fresh pass/skip counts, and verify CLI startup.
