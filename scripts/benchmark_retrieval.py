"""Reproducible retrieval ablations; generated distractors are not real-repo evidence."""

import argparse
import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo.config import load_project_env
from bingo.embeddings import FastEmbedEncoder, encoder_from_env
from bingo.retrieval import RetrievalEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", default="benchmarks/retrieval_tasks.json")
    parser.add_argument("--fixture", default="tests/fixtures/retrieval")
    parser.add_argument("--distractors", type=int, default=0)
    parser.add_argument("--output", default="artifacts/retrieval-ablation.json")
    parser.add_argument("--require-vector", action="store_true")
    args = parser.parse_args()
    load_project_env(Path.cwd())
    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    report = {"dataset": str(args.queries), "synthetic_distractors": args.distractors, "modes": {}}
    with tempfile.TemporaryDirectory(prefix="bingo-retrieval-bench-") as folder:
        root = Path(folder)
        shutil.copytree(args.fixture, root, dirs_exist_ok=True)
        for n in range(args.distractors):
            (root / f"distractor_{n}.py").write_text(f"def calculate_value_{n}(x):\n    return x * {n+1}\n", encoding="utf-8")
        encoder = encoder_from_env()
        if isinstance(encoder, FastEmbedEncoder) and encoder.cache_dir is None:
            # The corpus is intentionally temporary; reuse the project model cache instead of downloading per run.
            encoder.cache_dir = str((Path.cwd() / ".bingo/retrieval/models").resolve())
        engine = RetrievalEngine(root, encoder=encoder)
        try:
            started = time.monotonic()
            report["index"] = engine.index(complete=True)
            report["index_seconds"] = time.monotonic()-started
            if args.require_vector and (report["index"]["fallback_reason"] or report["index"]["vector_coverage"] < 1):
                raise RuntimeError("real embeddings required, but index is incomplete/unavailable")
            for mode in ("symbol", "keyword", "vector", "hybrid", "auto"):
                rows = []
                for item in queries:
                    result = engine.search(item["query"], mode=mode, top_k=5)
                    ranks = [{s["path"] for s in hit["sources"]} for hit in result["hits"]]
                    expected = set(item["paths"])
                    found = set().union(*ranks) if ranks else set()
                    rank = next((i for i, paths in enumerate(ranks, 1) if paths & expected), 0)
                    rows.append({"query": item["query"], "kind": item["kind"], "expected": sorted(expected),
                                 "found": sorted(found), "recall_at_5": len(found & expected)/len(expected) if expected else None,
                                 "reciprocal_rank": 1/rank if rank else 0,
                                 "correct_abstention": not found if not expected else None,
                                 "duration_ms": result["duration_ms"], "estimated_tokens": result["estimated_tokens"],
                                 "strategy": result["strategy"], "fallback_reason": result["fallback_reason"],
                                 "query_type": result["query_type"], "escalated": result["escalated"],
                                 "channels_used": result["channels_used"],
                                 "vector_backend": result["vector_backend"], "vector_coverage": result["vector_coverage"]})
                answerable = [r for r in rows if r["expected"]]
                durations = sorted(r["duration_ms"] for r in rows)
                report["modes"][mode] = {"recall_at_5": sum(r["recall_at_5"] for r in answerable)/max(1, len(answerable)),
                                         "mrr": sum(r["reciprocal_rank"] for r in answerable)/max(1, len(answerable)),
                                         "p95_ms": durations[max(0, math.ceil(len(durations)*.95)-1)], "rows": rows}
        finally:
            engine.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "index": report["index"], "modes": {m: {k: v for k, v in r.items() if k != "rows"} for m, r in report["modes"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
