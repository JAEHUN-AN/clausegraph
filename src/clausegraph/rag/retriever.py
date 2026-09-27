"""세 가지 검색 전략.

- **vector** — 문장 유사도만. 질문과 닮은 조각을 k개.
- **graph** — 구조만. 상품과 가입 시점이 정해지면 그 약관의 면책 조항을
  유사도 없이 전부 집어 온다.
- **hybrid** — 벡터로 들어가되, 걸린 조각이 속한 상품의 면책 조항을
  그래프로 끌어올린다.

이 프로젝트의 주장은 "면책은 부정 조건이라 벡터가 구조적으로 놓친다"는
것이다. 세 전략을 같은 질문에 걸어 recall 차이로 확인한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from neo4j import Driver
from pgvector.psycopg import register_vector

from ..access import Principal, visible
from ..graph.schema import OPEN_ENDED
from .embed import Embedder

DEFAULT_K = 10

_VECTOR_SEARCH = """
SELECT node_uid, node_kind, product, article_number, article_title,
       is_exclusion, content, 1 - (embedding <=> %s::vector) AS score
FROM clause_chunk
WHERE embedding IS NOT NULL
  AND (%s::text IS NULL OR effective_from = %s)
  -- 권한 게이트. NULL이면 제한 없음(적재·평가용), 아니면 그 목록 안에서만.
  -- **LIMIT보다 앞에서 걸러야 한다.** 뒤에서 걸러내면 k개를 뽑아 그중
  -- 스코프 밖을 버리게 되어, 권한이 좁을수록 결과가 조용히 적어진다.
  AND (%s::text[] IS NULL OR product = ANY(%s::text[]))
ORDER BY embedding <=> %s::vector
LIMIT %s
"""

# 상품과 시점이 정해지면 면책은 유사도 없이 구조로 가져온다.
_GRAPH_EXCLUSIONS = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
MATCH (a:Article:Exclusion)-[:IN_VERSION]->(v)
MATCH (a)-[:OF_PRODUCT]->(p:Product)
// 호출부가 준 목록과 주체의 스코프를 **둘 다** 만족해야 한다. 교집합은
// 파이썬에서 미리 낸다 — 여기서 두 목록을 받으면 질의가 권한을 아는
// 곳이 되고, 그러면 권한 규칙이 두 군데가 된다.
WHERE p.name IN $products
MATCH (a)-[:HAS_ITEM]->(i:Item)
RETURN i.uid AS node_uid, p.name AS product, a.number AS article_number,
       a.title AS article_title, i.text AS content, i.coverage AS coverage
"""


@dataclass(frozen=True)
class Hit:
    node_uid: str
    node_kind: str
    product: str
    article_number: str
    article_title: str
    is_exclusion: bool
    content: str
    score: float
    source: str


def connect_pg() -> psycopg.Connection:
    connection = psycopg.connect(os.environ["PG_DSN"])
    register_vector(connection)
    return connection


def search_vector(
    connection: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    k: int = DEFAULT_K,
    effective_from: str | None = None,
    principal: Principal,
) -> list[Hit]:
    """문장 유사도 k개. 주체가 못 보는 상품은 **질의에서** 빠진다.

    검색은 상품을 이름으로 지목하지 않으므로 거절할 것이 없다. 안 보이는
    것은 없는 것으로 둔다 — "검색했는데 권한 오류"는 쓸모가 없다.
    """
    vector = embedder.encode([query])[0]
    allowed = None if principal.unrestricted else sorted(principal.products)
    with connection.cursor() as cursor:
        cursor.execute(
            _VECTOR_SEARCH,
            (vector, effective_from, effective_from, allowed, allowed, vector, k),
        )
        return [
            Hit(
                node_uid=row[0],
                node_kind=row[1],
                product=row[2],
                article_number=row[3],
                article_title=row[4],
                is_exclusion=row[5],
                content=row[6],
                score=float(row[7]),
                source="vector",
            )
            for row in cursor.fetchall()
        ]


def search_graph(
    driver: Driver,
    products: list[str],
    *,
    on_date: str = OPEN_ENDED[:8],
    principal: Principal,
) -> list[Hit]:
    """상품의 면책 조항을 그 시점 기준으로 전부.

    호출부가 준 목록을 스코프와 교차한다. 교차 결과가 비면 빈 리스트다 —
    호출부가 이름을 지목했다기보다 **후보를 넘긴** 자리라 거절하지 않는다.
    """
    if not principal.unrestricted:
        products = [name for name in products if principal.can_see(name)]
    if not products:
        return []
    with driver.session() as session:
        records = session.run(_GRAPH_EXCLUSIONS, products=products, on_date=on_date)
        return [
            Hit(
                node_uid=record["node_uid"],
                node_kind="item",
                product=record["product"],
                article_number=record["article_number"],
                article_title=record["article_title"],
                is_exclusion=True,
                content=record["content"],
                score=0.0,
                source="graph",
            )
            for record in records
        ]


def search_hybrid(
    connection: psycopg.Connection,
    driver: Driver,
    embedder: Embedder,
    query: str,
    *,
    k: int = DEFAULT_K,
    on_date: str,
    effective_from: str | None = None,
    principal: Principal,
) -> list[Hit]:
    """벡터로 들어가 그래프로 넓힌다.

    벡터가 어느 상품 이야기인지는 대체로 맞힌다. 놓치는 것은 그 상품의
    **면책**이다. 그래서 걸린 상품들의 면책 조항을 구조로 끌어올린다.
    """
    seeds = search_vector(
        connection, embedder, query, k=k, effective_from=effective_from,
        principal=principal,
    )
    # **여기가 이 파일에서 가장 새기 쉬운 자리였다.** 상품 목록이 호출부가
    # 아니라 **벡터 결과에서** 나온다. 벡터를 안 막으면 스코프 밖 상품이
    # 씨앗으로 잡히고, 그 상품의 면책이 그래프로 통째로 끌려 올라온다.
    # 씨앗 한 건이 조항 수십 개로 번진다(notes/039).
    products = list(dict.fromkeys(hit.product for hit in seeds))

    merged: dict[str, Hit] = {hit.node_uid: hit for hit in seeds}
    for hit in search_graph(driver, products, on_date=on_date, principal=principal):
        merged.setdefault(hit.node_uid, hit)
    return visible(principal, list(merged.values()), product_of=lambda hit: hit.product)


# 순위 융합 상수. 원 논문(Cormack 2009)의 값이고, 이 규모에서 손댈 근거가 없다.
RRF_K = 60


def fuse_rrf(rankings: list[list[Hit]], *, limit: int | None = None) -> list[Hit]:
    """여러 순위를 RRF로 섞는다.

    **점수를 섞지 않고 순위만 섞는다.** 코사인 유사도는 0~1이고
    `ts_rank_cd`는 상한이 없다. 둘을 정규화해 가중합하려면 가중치를 어디선가
    정해야 하는데, 그 값을 정할 근거가 이 프로젝트에 없다 — 18문항으로
    맞추면 그 18문항에 맞춘 값이 된다.

    RRF는 각 순위에서 `1/(RRF_K + 순위)`를 더한다. 점수 공식이 달라도
    되고, 튜닝할 손잡이가 사실상 없다. 그래서 **측정이 정직해진다** —
    "섞었더니 좋아졌다"가 가중치를 만진 결과가 아니라는 것이 분명하다.
    """
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.node_uid] = scores.get(hit.node_uid, 0.0) + 1.0 / (RRF_K + rank)
            # 같은 청크가 두 순위에 있으면 먼저 본 쪽을 남긴다. 내용은 같고
            # `source`만 다르다.
            best.setdefault(hit.node_uid, hit)
    ordered = sorted(scores, key=lambda uid: scores[uid], reverse=True)
    if limit is not None:
        ordered = ordered[:limit]
    return [best[uid] for uid in ordered]
