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

from ..graph.schema import OPEN_ENDED
from .embed import Embedder

DEFAULT_K = 10

_VECTOR_SEARCH = """
SELECT node_uid, node_kind, product, article_number, article_title,
       is_exclusion, content, 1 - (embedding <=> %s::vector) AS score
FROM clause_chunk
WHERE embedding IS NOT NULL
  AND (%s::text IS NULL OR effective_from = %s)
ORDER BY embedding <=> %s::vector
LIMIT %s
"""

# 상품과 시점이 정해지면 면책은 유사도 없이 구조로 가져온다.
_GRAPH_EXCLUSIONS = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
MATCH (a:Article:Exclusion)-[:IN_VERSION]->(v)
MATCH (a)-[:OF_PRODUCT]->(p:Product)
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
) -> list[Hit]:
    vector = embedder.encode([query])[0]
    with connection.cursor() as cursor:
        cursor.execute(
            _VECTOR_SEARCH, (vector, effective_from, effective_from, vector, k)
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
    driver: Driver, products: list[str], *, on_date: str = OPEN_ENDED[:8]
) -> list[Hit]:
    """상품의 면책 조항을 그 시점 기준으로 전부."""
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
) -> list[Hit]:
    """벡터로 들어가 그래프로 넓힌다.

    벡터가 어느 상품 이야기인지는 대체로 맞힌다. 놓치는 것은 그 상품의
    **면책**이다. 그래서 걸린 상품들의 면책 조항을 구조로 끌어올린다.
    """
    seeds = search_vector(connection, embedder, query, k=k, effective_from=effective_from)
    products = list(dict.fromkeys(hit.product for hit in seeds))

    merged: dict[str, Hit] = {hit.node_uid: hit for hit in seeds}
    for hit in search_graph(driver, products, on_date=on_date):
        merged.setdefault(hit.node_uid, hit)
    return list(merged.values())
