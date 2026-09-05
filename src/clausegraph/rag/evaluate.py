"""면책 recall 측정 — vector / graph / hybrid.

    uv run --extra rag --extra onnx --extra graph python -m clausegraph.rag.evaluate

두 지표를 본다.

- **hit@k** — 지급 여부를 가르는 면책 조항을 하나라도 건졌는가.
- **recall** — 그 면책 조항의 모든 사본(상품·보장종목별)을 얼마나 건졌는가.
  실손은 같은 면책이 여러 상품·보장종목에 흩어져 있어, 하나만 찾고 끝내면
  다른 보장에 걸리는 사유를 놓친다.

검색 비용도 함께 적는다. 그래프는 recall이 높은 대신 후보를 많이 가져온다.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from neo4j import GraphDatabase

from .embed import get_embedder
from .retriever import connect_pg, search_graph, search_hybrid, search_vector

DEFAULT_K = 10
LATEST_DATE = "20260915"


@dataclass(frozen=True)
class Score:
    hits: int = 0
    recall_sum: float = 0.0
    candidates: list[int] | None = None
    latencies: list[float] | None = None


def evaluate(eval_path: Path, k: int) -> int:
    questions = json.loads(eval_path.read_text(encoding="utf-8"))["questions"]
    embedder = get_embedder()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )

    results: dict[str, dict[str, list]] = {
        name: {"hit": [], "recall": [], "candidates": [], "latency": []}
        for name in ("vector", "graph", "hybrid")
    }
    per_question: list[dict[str, object]] = []

    try:
        with connect_pg() as connection:
            for question in questions:
                gold = set(question["gold"])
                row: dict[str, object] = {"qid": question["qid"], "gold": len(gold)}

                for name, run in (
                    ("vector", lambda q=question: search_vector(
                        connection, embedder, q["query"], k=k)),
                    ("graph", lambda q=question: search_graph(
                        driver, q["products"], on_date=LATEST_DATE)),
                    ("hybrid", lambda q=question: search_hybrid(
                        connection, driver, embedder, q["query"], k=k, on_date=LATEST_DATE)),
                ):
                    started = time.perf_counter()
                    hits = run()
                    elapsed = time.perf_counter() - started

                    found = {hit.node_uid for hit in hits} & gold
                    results[name]["hit"].append(1 if found else 0)
                    results[name]["recall"].append(len(found) / len(gold))
                    results[name]["candidates"].append(len(hits))
                    results[name]["latency"].append(elapsed)
                    row[name] = f"{len(found)}/{len(gold)}"

                per_question.append(row)
    finally:
        driver.close()

    _report(results, per_question, questions, k)
    return 0


def _report(results, per_question, questions, k: int) -> None:
    print(f"\n=== 면책 recall (문항 {len(questions)}, k={k}) ===\n")
    print(f"{'전략':8s} {'hit@k':>8s} {'recall':>9s} {'후보 수':>9s} {'p50 지연':>10s}")
    for name in ("vector", "graph", "hybrid"):
        data = results[name]
        hit = sum(data["hit"]) / len(data["hit"])
        recall = sum(data["recall"]) / len(data["recall"])
        candidates = statistics.mean(data["candidates"])
        latency = statistics.median(data["latency"])
        print(
            f"{name:8s} {hit:8.1%} {recall:9.1%} {candidates:9.1f} {latency * 1000:9.0f}ms"
        )

    print("\n=== 문항별 (찾은 정답/전체 정답) ===")
    print(f"{'qid':>4s} {'gold':>5s} {'vector':>8s} {'graph':>8s} {'hybrid':>8s}  질문")
    lookup = {q["qid"]: q for q in questions}
    for row in per_question:
        query = lookup[row["qid"]]["query"]
        print(
            f"{row['qid']:4d} {row['gold']:5d} {row['vector']:>8s} "
            f"{row['graph']:>8s} {row['hybrid']:>8s}  {query[:38]}"
        )

    missed = [row for row in per_question if row["vector"].startswith("0/")]
    if missed:
        print(f"\n벡터가 통째로 놓친 문항 {len(missed)}개:")
        for row in missed:
            print(f"  [{row['qid']:2d}] {lookup[row['qid']]['query'][:52]}")
            print(f"       면책: {lookup[row['qid']]['note']}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="면책 recall 측정")
    parser.add_argument("--eval", type=Path, default=Path("data/eval/exclusion_recall.json"))
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()
    return evaluate(args.eval, args.k)


if __name__ == "__main__":
    raise SystemExit(main())
