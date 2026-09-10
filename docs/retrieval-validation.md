# Retrieval and context validation — 2026-09-09

Real local model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` through FastEmbed 0.8.0/ONNX. No generation-model API was used.

## Current workspace

The completed index run found 48 eligible files, 954 source chunks, and 209 Python symbols. It produced two bounded cards per symbol: 418 vectors, 100% coverage, no parse failures, and no provider fallback. The previous full-body vector table is emptied during schema migration.

A real semantic query, “找出负责解析 AST 并生成短向量卡的代码”, took the weak-first-pass escalation route and used `symbol + keyword + vector`. Its first three candidates were `_BehaviorVisitor`, `_call_name`, and `extract_symbols` in `bingo/retrieval_corpus.py`. An exact `symbol_cards` lookup followed by hash-checked `read_symbol` returned the current 1,247-character definition without truncation.

A relationship query, “who calls pack_hits”, used `symbol + graph`, expanded one hop, and returned the two `ContextManager` callers with `in:calls` metadata. Graph fan-out remained capped at eight.

## Reproducible fixture benchmark

The fixture has four functions and eight queries: exact identifiers, English semantics, Chinese cross-language queries, a call relationship, and one negative. This is an integration smoke set, not evidence of production-repository accuracy.

| Mode | Recall@5 | MRR | Observed P95 |
|---|---:|---:|---:|
| symbol | 0.714 | 0.643 | 13.81 ms |
| keyword | 0.714 | 0.643 | 10.65 ms |
| vector | 1.000 | 1.000 | 55.17 ms |
| hybrid | 1.000 | 1.000 | 12.35 ms |
| auto | 1.000 | 1.000 | 12.31 ms |

The benchmark reused the project model cache while keeping its corpus and SQLite index temporary. Query embeddings are cached across modes, so these P95 figures are smoke measurements rather than a fair cold-start comparison. Medium/large Symbol Card benchmarks and model-specific no-answer calibration remain outstanding.

## Regression coverage

Tests cover multi-granularity AST extraction, stable Symbol IDs, same-file and uniquely-resolved cross-file edges, persistent parse failures, bounded identity/behavior cards, exclusion of body literals from embeddings, exact Symbol routing, weak-evidence escalation, graph expansion, hash-checked Symbol reading, long-Symbol line boundaries, model/version invalidation, partial vector coverage, provider fallback, ANN fallback, path exclusions, junction safety, duplicate-source retention, compact retrieval history, context budgets, and checkpoint preservation as the tool catalog grows.

The context/memory separation tests additionally cover:

- code, memory, mixed, resume, and general Context Router decisions;
- Chinese Memory term matching and duplicate durable/session note removal;
- filtering legacy file-derived episodic notes from recall;
- omission of file summaries and the current request from Working State;
- Evidence Cache range coverage, SHA-256 freshness, path invalidation, LRU limits, oversized legacy state, reset, and write invalidation;
- `read_file` and `read_symbol` cache population without code promotion into episodic memory;
- Source Evidence injection only for fresh candidates;
- direct-retrieval/cache and history/Source Evidence deduplication;
- Checkpoint evidence references without source bodies;
- externally modified cache entries reported in Prompt metadata;
- multi-turn context scoring against the actual Prompt rather than one legacy section.

Final verification:

- Full suite: **181 passed, 1 skipped in 122.87 seconds**.
- Ruff: the new `context_router` and `evidence_cache` modules and their tests pass the full configured rule set; all changed Python files pass undefined-name and import-order checks. The repository's broader pre-existing style backlog was not treated as part of this feature.
- Module compilation and imports: passed.
- Editable package build/install with local build dependencies and `pip check`: passed.
- Standalone index/search and `read_symbol` smoke tests: passed.
- The skip is the existing Windows privileged-symlink test; the NTFS junction regression passes.
