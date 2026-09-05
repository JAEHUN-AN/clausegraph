"""2. 보장탐색 — 가입 시점에 적용되던 약관에서 보장 조항을 찾는다.

먼저 **버전을 고정한다.** 가입일이 정해지면 적용 약관이 정해지고, 그 뒤의
모든 조회는 그 버전 안에서만 이뤄져야 한다. 이 순서를 어기면 2025년
가입자에게 2026년 조항을 들이대게 된다(notes/006).
"""

from __future__ import annotations

from datetime import date

from neo4j import Driver

from .models import Evidence

_VERSION_RANGES = """
MATCH (v:Version)
RETURN v.effective_from AS effective_from, v.effective_to AS effective_to
ORDER BY v.effective_from
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

# 버전 구간과 상품별 보장 조항은 약관을 다시 적재할 때까지 바뀌지 않는다.
# 청구마다 Neo4j를 다시 때리면 그것이 그대로 지연이 된다(notes/011).
_VERSION_CACHE: list[tuple[str, str]] = []
_COVERAGE_CACHE: dict[tuple[str, str], tuple[Evidence, ...]] = {}


def clear_caches() -> None:
    """약관을 다시 적재한 뒤 부른다. 테스트에서도 쓴다."""
    _VERSION_CACHE.clear()
    _COVERAGE_CACHE.clear()


def _version_ranges(driver: Driver) -> list[tuple[str, str]]:
    if not _VERSION_CACHE:
        with driver.session() as session:
            _VERSION_CACHE.extend(
                (record["effective_from"], record["effective_to"])
                for record in session.run(_VERSION_RANGES)
            )
    return _VERSION_CACHE


def resolve_version(driver: Driver, enrolled_on: date) -> str | None:
    """가입일에 적용되던 약관 버전. 없으면 None.

    버전이 넷뿐이라 구간을 한 번 읽어 두고 프로세스 안에서 고른다.
    """
    on_date = enrolled_on.strftime("%Y%m%d")
    for effective_from, effective_to in _version_ranges(driver):
        if effective_from <= on_date < effective_to:
            return effective_from
    return None


def find_coverage(driver: Driver, product: str, version: str) -> tuple[Evidence, ...]:
    cache_key = (product, version)
    cached = _COVERAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with driver.session() as session:
        records = session.run(_COVERAGE_ARTICLES, product=product, version=version)
        cached = tuple(
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
    _COVERAGE_CACHE[cache_key] = cached
    return cached
