# Context Memory Retrieval Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate durable/episodic memory from repository retrieval and use a freshness-checked evidence cache to avoid repeated source reads.

**Architecture:** Add focused `EvidenceCache` and `ContextRouter` units. Keep legacy session fields readable, stop promoting file reads into recallable memory, and let `ContextManager` assemble recalled memory, code candidates, and cached source evidence as distinct sections.

**Tech Stack:** Python 3.10+, JSON session persistence, pytest, existing adaptive retrieval and context packer.

---

### Task 1: Evidence cache contract

**Files:**
- Create: `bingo/evidence_cache.py`
- Create: `tests/test_evidence_cache.py`

- [x] Write tests proving range/hash keyed reuse, LRU limits, path invalidation, stale-file rejection, and legacy-safe normalization.
- [x] Run the focused tests and confirm they fail because `EvidenceCache` does not exist.
- [x] Implement the bounded persistent cache and rerun the focused tests.

### Task 2: Memory responsibility cleanup

**Files:**
- Modify: `bingo/memory.py`
- Modify: `bingo/runtime.py`
- Modify: `tests/test_memory.py`
- Modify: `tests/test_bingo.py`

- [x] Write tests proving file summaries are absent from rendered Memory and code-derived notes are excluded from recall.
- [x] Run the focused tests and confirm the old behavior fails them.
- [x] Stop adding read summaries to episodic memory while preserving recent file references and old-session compatibility.
- [x] Add mixed Chinese/English memory tokenization coverage and rerun tests.

### Task 3: Adaptive context routing

**Files:**
- Create: `bingo/context_router.py`
- Create: `tests/test_context_router.py`

- [x] Write tests for code, memory, mixed, resume, and neutral queries plus feature flags.
- [x] Run tests and confirm the missing router causes failure.
- [x] Implement the explainable pure routing function and rerun tests.

### Task 4: Prompt source separation

**Files:**
- Modify: `bingo/context_manager.py`
- Modify: `bingo/runtime.py`
- Modify: `tests/test_context_manager.py`
- Modify: `tests/test_retrieval.py`

- [x] Write tests for distinct Recalled Memory, Retrieval Candidates, and Source Evidence sections.
- [x] Verify cached evidence is injected only when its fresh range overlaps a current retrieval hit.
- [x] Implement whole-block evidence packing, cross-source dedupe, budgets, and metadata.
- [x] Preserve legacy section-budget aliases and feature flags, then rerun focused tests.

### Task 5: Runtime cache updates and invalidation

**Files:**
- Modify: `bingo/runtime.py`
- Modify: `bingo/tools.py`
- Modify: `tests/test_bingo.py`
- Modify: `tests/test_symbol_retrieval.py`

- [x] Write tests proving successful `read_file` and `read_symbol` populate cache entries.
- [x] Write tests proving write/patch invalidates all entries for the changed path.
- [x] Implement cache metadata transfer from tool results without changing user-visible tool contracts.
- [x] Rerun focused runtime and retrieval tests.

### Task 6: Documentation and full verification

**Files:**
- Modify: `docs/retrieval.md`
- Modify: `docs/architecture/agent-harness-v1-overview.md`
- Modify: `docs/retrieval-validation.md`
- Modify: `README.md`

- [x] Document the final ownership model, lifecycle, route matrix, budgets, migration behavior, and interview explanation.
- [x] Run focused tests, the full test suite, Ruff on changed modules/tests, and compile/import checks.
- [x] Record exact fresh results in `docs/retrieval-validation.md` and mark every completed plan step.
