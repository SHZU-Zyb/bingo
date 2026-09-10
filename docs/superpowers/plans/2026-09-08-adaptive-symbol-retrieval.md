# Adaptive Symbol Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Bingo's adaptive code retrieval with a persistent symbol graph, bounded symbol-card embeddings, evidence-gated routing, and exact symbol reading.

**Architecture:** `retrieval_corpus.py` owns source filtering, AST extraction, symbol metadata, graph edges, and lexical indexes. `retrieval.py` starts with the cheapest suitable channel, normalizes keyword/vector/symbol candidates to stable symbol IDs, escalates only when first-pass evidence is weak, and returns compact source cards. `read_symbol` verifies the current file hash before returning exact source.

**Tech Stack:** Python AST, SQLite/FTS5, existing embedding adapters, weighted reciprocal-rank fusion, pytest.

---

### Task 1: Symbol extraction and persistence

**Files:**
- Modify: `bingo/retrieval_corpus.py`
- Test: `tests/test_symbol_retrieval.py`

- [x] Write failing tests showing class/function/method/nested-function nodes, stable IDs, signatures, bounded metadata, `contains`/`inherits`/statically-resolved `calls` edges, and syntax-error fallback.
- [x] Run `python -m pytest tests/test_symbol_retrieval.py -q` and confirm the new APIs are missing.
- [x] Add `symbols`, `symbol_edges`, and `symbol_lexical` schema; extract and incrementally replace symbols in the same file transaction as chunks.
- [x] Run the focused tests and confirm they pass.

### Task 2: Bounded symbol-card vectors

**Files:**
- Modify: `bingo/retrieval_corpus.py`
- Modify: `bingo/retrieval.py`
- Test: `tests/test_symbol_retrieval.py`

- [x] Write a failing test that captures embedding inputs and proves method bodies, secrets, hashes, and line numbers are absent while identity and deterministic behavior fields remain bounded.
- [x] Run the focused test and confirm the current full-code embedding input fails it.
- [x] Generate separate identity and behavior cards with fixed field order and caps; persist vectors by symbol ID, card kind, encoder identity, and schema version.
- [x] Run the focused tests and confirm incremental vector invalidation and partial coverage work.

### Task 3: Adaptive routing and fused symbol candidates

**Files:**
- Modify: `bingo/retrieval.py`
- Modify: `bingo/retrieval_cli.py`
- Test: `tests/test_symbol_retrieval.py`
- Test: `tests/test_retrieval.py`

- [x] Write failing tests for exact-symbol routing, semantic symbol-vector routing, weak first-pass escalation, graph-query expansion, path scope, per-file/per-parent caps, no-answer behavior, and routing diagnostics.
- [x] Run focused tests and confirm failures identify the missing strategy and metadata.
- [x] Add query classification, evidence gates, weighted RRF normalized to symbol IDs, bounded one-hop graph expansion, and explicit `symbol` mode.
- [x] Keep direct mode for fitting explicit scopes and retain keyword fallback when AST or embeddings are unavailable.
- [x] Run retrieval tests and adjust only expectations intentionally changed from source blocks to source cards.

### Task 4: Exact symbol reading

**Files:**
- Modify: `bingo/retrieval.py`
- Modify: `bingo/tools.py`
- Modify: `bingo/context_manager.py`
- Test: `tests/test_symbol_retrieval.py`
- Test: `tests/test_retrieval.py`

- [x] Write failing tests for `read_symbol`, hard path containment, stale-hash rejection, bounded long-symbol windows, and tool registration/validation.
- [x] Add a read-only `read_symbol` tool that accepts a stable symbol ID and returns current source only after hash verification.
- [x] Ensure retrieval history and automatic evidence preserve compact cards until the model explicitly reads a symbol.
- [x] Run focused runtime and context tests.

### Task 5: Documentation and verification

**Files:**
- Modify: `docs/retrieval.md`
- Modify: `README.md`
- Modify: `scripts/benchmark_retrieval.py`

- [x] Document the routing state machine, schema, card caps, graph confidence rules, fallbacks, security boundary, and trace fields.
- [x] Extend benchmark cases with exact symbols, behavior descriptions, graph relations, and no-answer queries; retain duplicate-name regression coverage in pytest.
- [x] Run focused tests, the full pytest suite, Ruff on retrieval-specific files, import/package smoke tests, and CLI index/search smoke tests.
- [x] Review the final implementation against every requirement in this plan and record remaining limitations without claiming unmeasured quality.
