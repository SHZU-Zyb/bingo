# Agent Harness v1 — Architecture Overview

## Core Components

### Task State

The task state module tracks agent progress through a structured state machine. It records tool invocations, step counts, stop reasons, and session metadata. Each task run produces a deterministic artifact suitable for benchmark comparisons.

### Workspace

The workspace context isolates agent operations within a designated root directory, preventing path traversal and ensuring tools only access files within the project boundary.

### Model Clients

Model-agnostic adapters translate between Bingo's internal protocol and provider-specific APIs (Ollama, OpenAI-compatible, Anthropic-compatible, DeepSeek).

### Context Sources

The agent keeps repository evidence separate from semantic memory:

1. Task state and checkpoints — the current goal, progress, blockers, next action, key files, and evidence references.
2. Recalled memory — durable decisions, conventions, user preferences, dependency facts, and reusable process observations.
3. Adaptive code retrieval — current repository locations selected through direct, Symbol, keyword, vector, and graph routes.
4. Evidence cache — bounded source reads keyed by path, line range, and file hash. It is execution cache, not long-term memory.
5. Source evidence — fresh cached source selected for the current query. Current source takes precedence over recalled facts.

Legacy `file_summaries` remain readable for session migration and stale-state detection, but new reads are stored only in the evidence cache and file summary bodies are not rendered in the Memory section.

### Context Manager

Uses an explainable Context Router, then assembles prefix/checkpoint, compact working state, recalled memory, retrieval candidates, source evidence, recent history, and the current request. Budgets are character based at the prompt boundary; candidates and evidence are packed as complete blocks.
