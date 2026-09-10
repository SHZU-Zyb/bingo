# Adaptive Code Retrieval Implementation Plan

**Goal:** Add genuine semantic code retrieval without requiring a model service for ordinary repository searches.

**Architecture:** A shared RetrievalEngine serves a CLI, a read-only retrieve_code tool and optional automatic prompt evidence. SQLite FTS5 stores code chunks and BM25 indexes; model-versioned embeddings are persisted separately in the same database. Retrieval output carries stable source coordinates, freshness hashes and routing diagnostics.

**Stack:** Python stdlib (AST, SQLite FTS5, urllib); optional FastEmbed and USearch HNSW. Existing character-based prompt budgets remain explicit; token estimates are conservative UTF-8 byte counts, not tokenizer-exact measurements.

## Decisions

- No external embedding request unless a provider is explicitly configured. Providers: none, ollama, openai, fastembed. API credentials use a separate BINGO_EMBED_API_KEY. FastEmbed runs locally; first use may download weights.
- auto routes a fitting explicit file/small scoped corpus to direct loading, identifiers to keyword, semantic queries to hybrid. Missing/unavailable embeddings degrade to keyword with an explicit reason. Forced hybrid never silently claims vector success.
- Python AST top-level definitions are chunked with symbol metadata; long definitions and other text use overlapping bounded line windows. Chunks retain exact original lines. Supported extensions and ignore rules exclude state, dependencies, secrets, generated data, binary files and symlinks. Gitignore semantics use pathspec.
- SQLite file hashes drive incremental replacement/deletion. Model identity changes invalidate vectors, not source chunks. Embedding batches and per-query indexing limits bound latency; metadata exposes incomplete coverage. Explicit index command can finish all batches.
- Small: <=200 chunks; medium: <=5000; large: above 5000 (configurable). Size labels describe indexed corpus, not benchmarked throughput. Large corpora can use optional HNSW; exact cosine fallback is explicit and memory bounded. HNSW is a derived in-memory index cached per engine/generation; SQLite remains canonical.
- RRF merges BM25 and vector rankings. Exact symbols take priority. Overlapping chunks are suppressed and identical content is grouped with all source locations retained. Pack complete source blocks under both byte-token and character budgets. No arbitrary snippet truncation.
- Automatic prompt retrieval is opt-in via --auto-retrieve; manual retrieve_code is always available. Preserve legacy prompt sections when disabled. Runtime metadata records routing and evidence sources. Existing search/read_file contracts remain unchanged.
- Session persistence and memory retrieval are separate from code retrieval. No whole-repository preloading; no unmeasured performance/quality claims.

## Execution and acceptance

- [x] Add failing tests in tests/test_retrieval.py: direct/symbol/semantic routes, semantic-only hit through an injected test encoder, incremental update/delete, changed encoder identity, vector failure, bounded output, ignored paths and escapes, AST source positions, API response validation.
- [x] Implement retrieval_corpus.py: filtering, chunking, persistent FTS and file revisions. Test changed/deleted and ignored files, including nested ignores.
- [x] Implement embeddings.py: validated real HTTP/FastEmbed encoders and normalization, never log credentials or provider response bodies on failures.
- [x] Implement retrieval.py: routing, vector persistence, fusion, exact/optional ANN search, provenance and budget packing. Add retrieval_cli.py for index/search and scale ablation reports.
- [x] Wire retrieve_code into tools.py; lazy engine construction in runtime.py; optional prompt evidence and metadata in context_manager.py; config flags in cli.py. Verify complete evidence survives the tool result boundary.
- [x] Add benchmark query fixture and executable ablation runner. Separate fixture encoder tests from real embedding smoke runs; report provider and coverage for every run.
- [x] Run focused tests, real local embedding smoke if weights are obtainable, full pytest, package install/import/CLI and dependency checks. Record results and limitations in docs/retrieval.md.

User authorized direct implementation without further questions. Current workspace has no functional Git metadata; changes are made in place and no commit is claimed.

## Implementation adjustments

- USearch supplies prebuilt Windows HNSW support; optional backend failures use exact cosine.
- Real multilingual FastEmbed ONNX model is installed and tested.
- Small/medium/large synthetic-scale experiments are separate from production claims.
- Low-similarity ANN evidence triggers exact fallback; threshold is configurable and requires dataset calibration.
- Review regressions cover complete direct-file loading, NTFS junction exclusion before embedding, and repeated-source budget packing.
