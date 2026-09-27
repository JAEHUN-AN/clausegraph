"""권한 누수 측정 — 스코프 밖 조항이 답에 새는가.

    uv run --extra rag --extra onnx --extra graph python -m clausegraph.access_eval

## 무엇을 재는가

주체마다 볼 수 있는 상품이 정해져 있다. **그 밖의 상품 조항이 답에 한 건이라도
섞이면 누수다.** 라벨이 필요 없다 — 스코프가 정답을 정의한다(notes/033에서
쓴 방식과 같다).

두 번 잰다.

| | 어떻게 | 무엇을 뜻하나 |
|---|---|---|
| 게이트 전 | 제한 없는 주체로 부르고 스코프 기준으로 센다 | 게이트를 달기 전 코드가 내주던 것 |
| 게이트 후 | 그 주체로 부른다 | 지금 코드가 내주는 것 |

게이트가 유일한 차이라서 두 값의 차가 곧 게이트가 막은 양이다.

## 왜 경로마다 따로 재는가

notes/023의 교훈이 이것이었다 — **감싸는 줄 알았던 것이 두 번째 구현이었다.**
게이트를 한 곳에 걸고 "막았다"고 말하면 나머지 경로가 조용히 샌다. 그래서
경로를 하나씩 세우고 각각을 잰다. 한 줄이라도 0이 아니면 그 줄이 구멍이다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from neo4j import GraphDatabase

from .access import AccessDeniedError, Principal
from .agents.coverage import find_coverage
from .agents.exclusion import enumerate_exclusions
from .rag.embed import get_embedder
from .rag.lexical import search_lexical
from .rag.retriever import connect_pg, search_graph, search_hybrid, search_vector

DEFAULT_EVAL = Path("data/eval/exclusion_recall.json")
LATEST_DATE = "20260915"
K = 10

# 실제 업무에서 권한이 갈리는 모양으로 잡았다. 상품 16개를 넷으로 나눈다.
SCOPES: dict[str, frozenset[str]] = {
    "실손심사": frozenset({
        "기본형 실손의료보험(급여 실손의료비)",
        "실손의료보험 특별약관(비급여 실손의료비)",
        "실손의료보험 특별약관1(중증 비급여 실손의료비)",
        "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
        "기본형 해외여행 실손의료보험",
        "해외여행 실손의료보험 특별약관",
        "해외여행 실손의료보험 특별약관1(중증 비급여 실손의료비)",
        "해외여행 실손의료보험 특별약관2(비중증 비급여 실손의료비)",
    }),
    "자동차손사": frozenset({"자동차보험"}),
    "생명제휴": frozenset({"생명보험"}),
    "일반손보": frozenset({
        "화재보험", "배상책임보험", "신용보험", "신원보증보험", "채무이행보증보험",
    }),
}

OPEN = Principal.everything("측정")


@dataclass
class Leak:
    path: str
    before: int = 0
    after: int = 0
    denied: int = 0
    # 새어 나온 상품 이름. 수만 세면 "무엇이 샜는지"를 못 말한다.
    products: frozenset[str] = frozenset()


def _count(rows, product_of, scope: frozenset[str]) -> tuple[int, frozenset[str]]:
    outside = [product_of(row) for row in rows if product_of(row) not in scope]
    return len(outside), frozenset(outside)


def measure(eval_path: Path) -> int:
    questions = json.loads(eval_path.read_text(encoding="utf-8"))["questions"]
    embedder = get_embedder()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    leaks: dict[str, Leak] = {}

    def record(path: str, rows_open, rows_gated, product_of, scope) -> None:
        leak = leaks.setdefault(path, Leak(path=path))
        before, names = _count(rows_open, product_of, scope)
        after, _ = _count(rows_gated, product_of, scope)
        leak.before += before
        leak.after += after
        leak.products |= names

    hit_product = lambda hit: hit.product  # noqa: E731

    try:
        with connect_pg() as connection:
            for label, scope in SCOPES.items():
                who = Principal(name=label, products=scope)
                for question in questions:
                    query = question["query"]
                    seeds = question["products"]

                    record(
                        "search_vector",
                        search_vector(connection, embedder, query, k=K, principal=OPEN),
                        search_vector(connection, embedder, query, k=K, principal=who),
                        hit_product, scope,
                    )
                    record(
                        "search_lexical",
                        search_lexical(connection, query, k=K, principal=OPEN),
                        search_lexical(connection, query, k=K, principal=who),
                        hit_product, scope,
                    )
                    record(
                        "search_graph",
                        search_graph(driver, seeds, on_date=LATEST_DATE, principal=OPEN),
                        search_graph(driver, seeds, on_date=LATEST_DATE, principal=who),
                        hit_product, scope,
                    )
                    record(
                        "search_hybrid",
                        search_hybrid(connection, driver, embedder, query, k=K,
                                      on_date=LATEST_DATE, principal=OPEN),
                        search_hybrid(connection, driver, embedder, query, k=K,
                                      on_date=LATEST_DATE, principal=who),
                        hit_product, scope,
                    )

                # 이름으로 지목하는 경로는 건수가 아니라 **거절되는가**를 본다.
                for product in sorted({p for s in SCOPES.values() for p in s}):
                    for path, call in (
                        ("find_coverage", lambda pr, w: find_coverage(
                            driver, pr, "20260506", principal=w)),
                        ("enumerate_exclusions", lambda pr, w: enumerate_exclusions(
                            driver, pr, "20260506", principal=w)),
                    ):
                        leak = leaks.setdefault(path, Leak(path=path))
                        if product in scope:
                            continue
                        # 게이트 전: 남의 상품을 이름으로 불러도 그냥 나왔다.
                        leak.before += len(call(product, OPEN))
                        leak.products |= {product}
                        try:
                            leak.after += len(call(product, who))
                        except AccessDeniedError:
                            leak.denied += 1
    finally:
        driver.close()

    _report(leaks, len(questions))
    return 0


def _report(leaks: dict[str, Leak], questions: int) -> None:
    print(f"\n=== 권한 누수 (주체 {len(SCOPES)} × 문항 {questions}) ===\n")
    print(f"{'경로':22s} {'게이트 전':>10s} {'게이트 후':>10s} {'거절':>7s}  샌 상품")
    for leak in leaks.values():
        names = ", ".join(sorted(leak.products)[:2])
        more = f" 외 {len(leak.products) - 2}" if len(leak.products) > 2 else ""
        print(
            f"{leak.path:22s} {leak.before:10d} {leak.after:10d} "
            f"{leak.denied:7d}  {names[:40]}{more}"
        )
    total_before = sum(leak.before for leak in leaks.values())
    total_after = sum(leak.after for leak in leaks.values())
    print(f"\n{'합계':22s} {total_before:10d} {total_after:10d}")
    if total_after:
        print("\n**게이트 후에 0이 아닌 경로가 있다. 그 경로가 구멍이다.**")
    else:
        print("\n게이트 후 누수 0. 다만 이것은 **측정한 경로에 대해서만** 참이다.")


# 게이트를 어디에 걸었느냐에 따른 시나리오. notes/023의 재현이다 —
# "감싸는 줄 알았던 것이 두 번째 구현이었다".
SCENARIOS = {
    "게이트 없음": frozenset(),
    "에이전트 층만": frozenset({"agent"}),
    "벡터만": frozenset({"vector"}),
    "검색 경로 전부": frozenset({"vector", "lexical", "graph"}),
    "전부": frozenset({"vector", "lexical", "graph", "agent"}),
}


def scenarios(eval_path: Path) -> int:
    """게이트를 어디까지 걸었을 때 얼마가 새는지.

    경로마다 주체를 `who`로 줄지 `OPEN`으로 줄지만 바꾼다. `OPEN`을 주면
    그 경로에 게이트가 없는 것과 같다 — 코드를 지웠다 붙였다 하지 않고
    같은 코드로 다섯 상태를 잰다.
    """
    questions = json.loads(eval_path.read_text(encoding="utf-8"))["questions"]
    embedder = get_embedder()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    table: list[tuple[str, dict[str, int]]] = []
    try:
        with connect_pg() as connection:
            for name, gated in SCENARIOS.items():
                counts = {"vector": 0, "lexical": 0, "graph": 0, "hybrid": 0, "agent": 0}
                for label, scope in SCOPES.items():
                    who = Principal(name=label, products=scope)
                    pick = lambda leg, w=who, g=gated: w if leg in g else OPEN  # noqa: E731
                    for question in questions:
                        query, seeds = question["query"], question["products"]
                        counts["vector"] += _count(
                            search_vector(connection, embedder, query, k=K,
                                          principal=pick("vector")),
                            lambda hit: hit.product, scope)[0]
                        counts["lexical"] += _count(
                            search_lexical(connection, query, k=K,
                                           principal=pick("lexical")),
                            lambda hit: hit.product, scope)[0]
                        counts["graph"] += _count(
                            search_graph(driver, seeds, on_date=LATEST_DATE,
                                         principal=pick("graph")),
                            lambda hit: hit.product, scope)[0]
                        # 하이브리드는 다리 둘을 각각의 상태로 부른다. 이
                        # 경로에 자기 게이트는 없다 — 다리에서 물려받는다.
                        counts["hybrid"] += _count(
                            _hybrid_legs(connection, driver, embedder, query,
                                         pick("vector"), pick("graph")),
                            lambda hit: hit.product, scope)[0]
                    for product in sorted({p for sc in SCOPES.values() for p in sc}):
                        if product in scope:
                            continue
                        # 거절되면 0건. 이 시나리오에서 세는 것은 "새어
                        # 나온 조항 수"이므로 거절은 그냥 더하지 않는다.
                        with suppress(AccessDeniedError):
                            counts["agent"] += len(enumerate_exclusions(
                                driver, product, "20260506", principal=pick("agent")))
                table.append((name, counts))
    finally:
        driver.close()

    print(f"\n=== 게이트를 어디에 거느냐 (주체 {len(SCOPES)} × 문항 {len(questions)}) ===\n")
    legs = ("vector", "lexical", "graph", "hybrid", "agent")
    head = " ".join(f"{leg:>9s}" for leg in legs)
    print(f"{'시나리오':18s} {head} {'합계':>9s}")
    for name, counts in table:
        cells = " ".join(f"{counts[leg]:9d}" for leg in legs)
        print(f"{name:18s} {cells} {sum(counts.values()):9d}")
    return 0


def _hybrid_legs(connection, driver, embedder, query, vector_who, graph_who):
    """`search_hybrid`를 다리별 주체로 흉내 낸다.

    실제 함수는 주체를 하나만 받는다. 여기서는 **벡터 다리에만 게이트가
    있고 그래프 다리에는 없는** 상태를 재야 하므로 안을 펼친다.
    """
    seeds = search_vector(connection, embedder, query, k=K, principal=vector_who)
    products = list(dict.fromkeys(hit.product for hit in seeds))
    merged = {hit.node_uid: hit for hit in seeds}
    for hit in search_graph(driver, products, on_date=LATEST_DATE, principal=graph_who):
        merged.setdefault(hit.node_uid, hit)
    return list(merged.values())


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="권한 누수 측정")
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument(
        "--scenarios",
        action="store_true",
        help="게이트를 어디까지 걸었을 때 얼마가 새는지",
    )
    args = parser.parse_args()
    return scenarios(args.eval) if args.scenarios else measure(args.eval)


if __name__ == "__main__":
    raise SystemExit(main())
