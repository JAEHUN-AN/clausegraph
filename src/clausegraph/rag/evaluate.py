"""면책 recall 측정 — 검색 전략별.

    uv run --extra rag --extra onnx --extra graph python -m clausegraph.rag.evaluate

두 지표를 본다.

- **hit@k** — 지급 여부를 가르는 면책 조항을 하나라도 건졌는가.
- **recall** — 그 면책 조항의 모든 사본(상품·보장종목별)을 얼마나 건졌는가.
  실손은 같은 면책이 여러 상품·보장종목에 흩어져 있어, 하나만 찾고 끝내면
  다른 보장에 걸리는 사유를 놓친다.

검색 비용도 함께 적는다. 그래프는 recall이 높은 대신 후보를 많이 가져온다.

## 전략

| 이름 | 무엇인가 |
|---|---|
| `vector` | 문장 유사도 k개 |
| `lexical` | 어간 일치 k개 (ts_rank_cd) |
| `lexical-df` | 문서빈도 낮은 어간만으로 질의 |
| `lexical-pfx` | 거기에 앞자리 일치(`:*`)까지 |
| `graph` | 그 상품·그 시점의 면책을 유사도 없이 전부 |
| `vec+graph` | 예전 `hybrid`. 벡터로 들어가 그래프로 넓힌다 |
| `vec+lex` | 벡터와 어휘를 RRF로 섞는다 |
| `+rerank` | 위 후보를 교차 인코더로 다시 세운다 |

`vec+graph`는 README와 notes/008에서 `hybrid`라 부르던 것이다. 공고와
업계에서 "하이브리드"는 **어휘+벡터**를 뜻하므로 이름을 갈랐다
(notes/038). 같은 것을 두 이름으로 부르지 않으려는 것이지 동작은 그대로다.
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

from ..access import Principal
from .embed import get_embedder
from .lexical import search_lexical
from .retriever import connect_pg, fuse_rrf, search_graph, search_hybrid, search_vector

DEFAULT_K = 10
LATEST_DATE = "20260915"

# recall을 재는 자리라 스코프로 결과가 줄면 측정이 흐려진다. 권한을 재는
# 자리는 `access_eval.py`다 (notes/039).
EVAL_PRINCIPAL = Principal.everything("recall-eval")


@dataclass(frozen=True)
class Score:
    hits: int = 0
    recall_sum: float = 0.0
    candidates: list[int] | None = None
    latencies: list[float] | None = None


# 리랭커에 줄 후보 수. 벡터의 recall@100이 88%라 **정답이 이 안에 있다.**
# 리랭킹이 랭킹 문제인지 아닌지를 가르려면 정답이 든 후보를 줘야 한다.
RERANK_POOL = 100


def _strategies(connection, driver, embedder, k: int, with_rerank: bool):
    """(이름, 실행) 목록. 여기 한 줄을 더하는 것이 전략을 하나 더 재는 일이다."""

    def vector(q):
        return search_vector(
            connection, embedder, q["query"], k=k, principal=EVAL_PRINCIPAL
        )

    def lexical(q):
        return search_lexical(connection, q["query"], k=k, principal=EVAL_PRINCIPAL)

    def lexical_df(q):
        return search_lexical(
            connection, q["query"], k=k, distinctive_only=True,
            principal=EVAL_PRINCIPAL,
        )

    def lexical_pfx(q):
        return search_lexical(
            connection, q["query"], k=k, distinctive_only=True, prefix=True,
            principal=EVAL_PRINCIPAL,
        )

    def graph(q):
        return search_graph(
            driver, q["products"], on_date=LATEST_DATE, principal=EVAL_PRINCIPAL
        )

    def vec_graph(q):
        return search_hybrid(
            connection, driver, embedder, q["query"], k=k, on_date=LATEST_DATE,
            principal=EVAL_PRINCIPAL,
        )

    def vec_lex(q):
        # 각 다리에서 k개씩 뽑아 섞고, 후보 수를 벡터 단독과 맞추려고 k로
        # 자른다. 자르지 않으면 "후보를 두 배 줬더니 좋아졌다"가 되어
        # 무엇이 이겼는지 알 수 없다.
        return fuse_rrf([vector(q), lexical_pfx(q)], limit=k)

    strategies = [
        ("vector", vector),
        ("lexical", lexical),
        ("lexical-df", lexical_df),
        ("lexical-pfx", lexical_pfx),
        ("graph", graph),
        ("vec+graph", vec_graph),
        ("vec+lex", vec_lex),
    ]
    if not with_rerank:
        return tuple(strategies)

    from .rerank import rerank

    def vec_rr(q):
        """벡터 후보 100개를 다시 세워 10개로 줄인다.

        **이것이 주장을 시험하는 자리다.** 정답은 이미 후보 안에 있다
        (recall@100 = 88%). 교차 인코더가 그걸 위로 끌어올리면 랭킹 문제가
        맞고, 못 끌어올리면 열거가 맞다.
        """
        pool = search_vector(
            connection, embedder, q["query"], k=RERANK_POOL,
            principal=EVAL_PRINCIPAL,
        )
        return rerank(q["query"], pool, limit=k)

    def graph_rr(q):
        """그래프 열거를 다시 세워 10개로 줄인다.

        그래프는 recall 100%인데 후보를 106개 준다. 심사자에게 106개를
        내미는 것은 아무것도 안 준 것에 가깝다. 리랭커가 100%를 지키면서
        10개로 줄일 수 있다면, 리랭킹의 값어치는 **찾는 데가 아니라
        추리는 데** 있다.
        """
        pool = search_graph(
            driver, q["products"], on_date=LATEST_DATE, principal=EVAL_PRINCIPAL
        )
        return rerank(q["query"], pool, limit=k)

    strategies.extend([("vec100+rr", vec_rr), ("graph+rr", graph_rr)])
    return tuple(strategies)


def evaluate(eval_path: Path, k: int, with_rerank: bool = False) -> int:
    questions = json.loads(eval_path.read_text(encoding="utf-8"))["questions"]
    embedder = get_embedder()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )

    try:
        with connect_pg() as connection:
            strategies = _strategies(connection, driver, embedder, k, with_rerank)
            names = [name for name, _ in strategies]
            results: dict[str, dict[str, list]] = {
                name: {"hit": [], "recall": [], "candidates": [], "latency": []}
                for name in names
            }
            per_question: list[dict[str, object]] = []

            for question in questions:
                gold = set(question["gold"])
                row: dict[str, object] = {"qid": question["qid"], "gold": len(gold)}

                for name, run in strategies:
                    started = time.perf_counter()
                    hits = run(question)
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

    _report(results, per_question, questions, k, names)
    return 0


def _report(results, per_question, questions, k: int, names: list[str]) -> None:
    print(f"\n=== 면책 recall (문항 {len(questions)}, k={k}) ===\n")
    print(f"{'전략':12s} {'hit@k':>8s} {'recall':>9s} {'후보 수':>9s} {'p50 지연':>10s}")
    for name in names:
        data = results[name]
        hit = sum(data["hit"]) / len(data["hit"])
        recall = sum(data["recall"]) / len(data["recall"])
        candidates = statistics.mean(data["candidates"])
        latency = statistics.median(data["latency"])
        print(
            f"{name:12s} {hit:8.1%} {recall:9.1%} {candidates:9.1f} "
            f"{latency * 1000:9.0f}ms"
        )

    print("\n=== 문항별 (찾은 정답/전체 정답) ===")
    header = " ".join(f"{name:>11s}" for name in names)
    print(f"{'qid':>4s} {'gold':>5s} {header}  질문")
    lookup = {q["qid"]: q for q in questions}
    for row in per_question:
        query = lookup[row["qid"]]["query"]
        cells = " ".join(f"{row[name]:>11s}" for name in names)
        print(f"{row['qid']:4d} {row['gold']:5d} {cells}  {query[:30]}")

    missed = [row for row in per_question if row["vector"].startswith("0/")]
    if missed:
        print(f"\n벡터가 통째로 놓친 문항 {len(missed)}개 — 다른 전략은:")
        for row in missed:
            others = "  ".join(
                f"{name} {row[name]}" for name in names if name != "vector"
            )
            print(f"  [{row['qid']:2d}] {lookup[row['qid']]['query'][:46]}")
            print(f"       면책: {lookup[row['qid']]['note']}")
            print(f"       {others}")


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
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="교차 인코더 리랭킹까지 잰다 (CPU에서 수 분)",
    )
    args = parser.parse_args()
    return evaluate(args.eval, args.k, args.rerank)


if __name__ == "__main__":
    raise SystemExit(main())
