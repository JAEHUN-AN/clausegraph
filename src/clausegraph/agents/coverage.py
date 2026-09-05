"""2. 보장탐색 — 가입 시점에 적용되던 약관에서 보장 조항을 찾는다.

먼저 **버전을 고정한다.** 가입일이 정해지면 적용 약관이 정해지고, 그 뒤의
모든 조회는 그 버전 안에서만 이뤄져야 한다. 이 순서를 어기면 2025년
가입자에게 2026년 조항을 들이대게 된다(notes/006).
"""

from __future__ import annotations

from datetime import date

from neo4j import Driver

from .models import Evidence

_VERSION_AT = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
RETURN v.effective_from AS effective_from
"""

_COVERAGE_ARTICLES = """
MATCH (v:Version {effective_from: $version})<-[:IN_VERSION]-(a:Article)
MATCH (a)-[:OF_PRODUCT]->(p:Product {name: $product})
WHERE NOT a:Exclusion
  AND (a.title CONTAINS '지급사유' OR a.title CONTAINS '보상내용'
       OR a.title CONTAINS '보험금의 지급' OR a.title CONTAINS '보장종목')
RETURN a.uid AS node_uid, a.number AS number, a.title AS title, a.text AS text
ORDER BY toInteger(split(a.number, '의')[0])
"""

QUOTE_CHARS = 160


def resolve_version(driver: Driver, enrolled_on: date) -> str | None:
    """가입일에 적용되던 약관 버전. 없으면 None."""
    with driver.session() as session:
        record = session.run(_VERSION_AT, on_date=enrolled_on.strftime("%Y%m%d")).single()
        return record["effective_from"] if record else None


def find_coverage(driver: Driver, product: str, version: str) -> tuple[Evidence, ...]:
    with driver.session() as session:
        records = session.run(_COVERAGE_ARTICLES, product=product, version=version)
        return tuple(
            Evidence(
                node_uid=record["node_uid"],
                product=product,
                article_number=record["number"],
                article_title=record["title"],
                quote=record["text"][:QUOTE_CHARS].strip(),
                role="coverage",
            )
            for record in records
        )
