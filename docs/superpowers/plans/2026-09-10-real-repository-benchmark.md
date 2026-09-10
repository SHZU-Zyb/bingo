# Real Repository Benchmark Implementation Plan

**Goal:** Add a reproducible benchmark that measures repository scale, end-to-end Agent outcomes, LLM/tool cost, and retrieval ablations on pinned real repositories while retaining raw artifacts and an interview-ready report.

**Architecture:** A versioned manifest declares repositories, exact revisions, mutation/setup steps, deterministic verifiers, and retrieval ground truth. A real-repository evaluator copies each repository into an isolated workspace, runs bounded Agent tasks, counts parent and child model calls from traces, and records full verifier logs. A retrieval evaluator indexes the same snapshots and compares Vector, Hybrid, and Auto under one warm-up/repetition protocol. A report renderer combines both artifacts and labels incomplete or synthetic evidence explicitly.

**Tech Stack:** Python 3.10 standard library, existing Bingo Runtime and RetrievalEngine, SQLite/FastEmbed optional backends, pytest, JSON/Markdown artifacts.

### Task 1: Manifest and repository inventory

- [x] Add failing tests for schema validation, repository path/revision checks, source-file/physical-LoC inventory, exclusions, and snapshot fingerprints.
- [x] Implement `bingo/real_benchmark.py` manifest loading and inventory records.
- [x] Run focused tests.

### Task 2: End-to-end evaluator

- [x] Add failing tests for isolated repository copies, deterministic setup mutations, verifier-based completion, parent/child LLM-call counting, timing, raw-log retention, and aggregate metrics.
- [x] Implement the evaluator with an injected model-client factory and reproducibility metadata.
- [x] Run focused tests.

### Task 3: Retrieval ablation

- [x] Add failing tests for per-repository ground truth, Vector/Hybrid/Auto metrics, macro aggregation, no-answer accuracy, warmed P95, and improvement calculations.
- [x] Implement retrieval benchmarking with injectable engines and the real embedding backend.
- [x] Run focused tests.

### Task 4: CLI, manifests, and reports

- [x] Add a CLI supporting inventory, end-to-end, retrieval, report, and all modes.
- [x] Add a checked-in Bingo real-repository manifest and grounded query set.
- [x] Render Markdown with safe resume metrics, protocol details, limitations, and links to retained artifacts.
- [x] Document how to add pinned repositories and tasks.

### Task 5: Validation

- [x] Run deterministic end-to-end smoke validation on an isolated copy.
- [x] Run real-code retrieval ablation when the local embedding model is available.
- [x] Retain JSON and Markdown reports under `artifacts/` and `docs/metrics/`.
- [x] Run focused tests, Ruff, compilation, CLI smoke, and the complete pytest suite.
