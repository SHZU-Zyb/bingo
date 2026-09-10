# Bingo Review Pack

## Project pitch

Bingo is a small, local coding agent designed for Ollama, OpenAI-compatible, Anthropic-compatible, and DeepSeek models. It runs entirely on your machine with no external dependencies.

## Architecture map

The agent harness is structured around a modular runtime with workspace management, task state tracking, and model-agnostic client adapters.

## Benchmark evidence

Benchmark results are captured in `benchmarks/benchmark-v1.json` and `artifacts/harness-regression-v2.json`. Each run uses a fresh fixture copy with deterministic scripted model outputs to ensure reproducibility.

## Sample run artifact list

- `trace.jsonl` — Full event trace of the agent run
- `report.json` — Structured report with metadata
- `task_state.json` — Final task state snapshot
