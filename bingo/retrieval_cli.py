"""Standalone indexing/search without invoking a generative model."""

import argparse
import json

from .config import load_project_env
from .retrieval import RetrievalEngine


def main(argv=None):
    parser = argparse.ArgumentParser(description="Index and retrieve code with adaptive hybrid RAG.")
    parser.add_argument("--cwd", default=".")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Persist all missing embeddings in bounded batches.")
    index.add_argument("--require-vector", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--path", default=".")
    search.add_argument("--mode", choices=("auto", "direct", "symbol", "keyword", "hybrid", "vector"), default="auto")
    search.add_argument("--budget-chars", type=int, default=4000)
    search.add_argument("--budget-tokens", type=int, default=4000)
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    load_project_env(args.cwd)
    engine = RetrievalEngine.from_env(args.cwd)
    try:
        if args.command == "index":
            result = engine.index(complete=True)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if args.require_vector and (result["fallback_reason"] or result["vector_coverage"] < 1):
                return 2
        else:
            result = engine.search(args.query, args.path, args.mode, args.budget_chars, args.budget_tokens, args.top_k)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"strategy={result['strategy']} reason={result['route_reason']} "
                      f"tier={result['corpus_tier']} coverage={result['vector_coverage']:.1%} "
                      f"backend={result['vector_backend']} fallback={result['fallback_reason'] or 'none'}")
                print(result["text"] or "No code evidence within budget.")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
