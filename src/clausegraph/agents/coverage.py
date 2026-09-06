"""2. 보장탐색 — 가입 시점에 적용되던 약관에서 보장 조항을 찾는다.

먼저 **버전을 고정한다.** 가입일이 정해지면 적용 약관이 정해지고, 그 뒤의
모든 조회는 그 버전 안에서만 이뤄져야 한다. 이 순서를 어기면 2025년
가입자에게 2026년 조항을 들이대게 된다(notes/006).

버전을 고르는 기준은 세칙 시행일이 **아니다.** 부칙이 약관의 적용일을
따로 정하고, 그 적용일이 상품마다 다르다.

    2026-05-06 개정의 부칙:
    "[별표15] 표준약관(개인실손의료보험은 제외한다) 개정내용은
     2026년 6월 6일 이후 체결되는 보험계약부터 적용한다"

즉 같은 개정이 생명보험에는 6월 6일부터, 실손에는 (그 부칙이 빼 두었으므로)
시행일부터 적용된다. **버전 선택이 상품별로 갈린다**(notes/016).
"""

from __future__ import annotations

from datetime import date

from neo4j import Driver

from ..law.appendix import Provision, same_product
from .models import Evidence
from .quote import prose_quote

_VERSION_ROWS = """
MATCH (v:Version)
OPTIONAL MATCH (v)-[:HAS_PROVISION {is_own: true}]->(p:Provision)
RETURN v.effective_from AS effective_from,
       p.applies_from AS applies_from,
       coalesce(p.included_products, []) AS included_products,
       coalesce(p.excluded_products, []) AS excluded_products
ORDER BY v.effective_from
"""

_ARTICLE_SCOPED_PROVISIONS = """
MATCH (v:Version {effective_from: $version})-[:HAS_PROVISION]->(p:Provision)
WHERE p.article_scoped
RETURN DISTINCT p.promulgated_on AS promulgated_on,
       coalesce(p.article_scope_notes, []) AS notes,
       coalesce(p.article_scope_products, []) AS products
ORDER BY promulgated_on DESC
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

# 버전이 넷뿐이라 구간을 한 번 읽어 두고 프로세스 안에서 고른다.
_VERSION_CACHE: list[dict[str, object]] = []
_COVERAGE_CACHE: dict[tuple[str, str], tuple[Evidence, ...]] = {}


def clear_caches() -> None:
    """약관을 다시 적재한 뒤 부른다. 테스트에서도 쓴다."""
    _VERSION_CACHE.clear()
    _COVERAGE_CACHE.clear()


def _version_rows(driver: Driver) -> list[dict[str, object]]:
    """버전과 그 자신의 부칙. 시행일자 순으로 정렬돼 있다."""
    if not _VERSION_CACHE:
        with driver.session() as session:
            _VERSION_CACHE.extend(dict(record) for record in session.run(_VERSION_ROWS))
    return _VERSION_CACHE


def applies_from(row: dict[str, object], product: str | None) -> str:
    """이 버전이 그 상품에 적용되기 시작하는 날.

    부칙이 적용일을 미뤘더라도 그 상품을 빼 두었으면 시행일부터 적용된다.
    """
    effective_from = str(row["effective_from"])
    deferred = row.get("applies_from")
    if not deferred:
        return effective_from
    if product is None:
        return str(deferred)

    scope = Provision(
        promulgated_on="",
        new_contracts_only=True,
        candidate_dates=(),
        text="",
        included_products=tuple(row.get("included_products") or ()),
        excluded_products=tuple(row.get("excluded_products") or ()),
    )
    return str(deferred) if scope.covers_product(product) else effective_from


def resolve_version(
    driver: Driver, enrolled_on: date, product: str | None = None
) -> str | None:
    """가입일에 그 상품에 적용되던 약관 버전. 없으면 None.

    `product`를 주지 않으면 부칙의 적용일을 상품 구분 없이 쓴다 — 개요용이며,
    판정에는 반드시 상품을 함께 넘겨야 한다.
    """
    on_date = enrolled_on.strftime("%Y%m%d")
    starts = sorted(
        (applies_from(row, product), str(row["effective_from"]))
        for row in _version_rows(driver)
    )

    chosen = None
    for start, version in starts:
        if start <= on_date:
            chosen = version
        else:
            break
    return chosen


def article_scoped_notes(driver: Driver, version: str, product: str) -> tuple[str, ...]:
    """그 판본에서 **조문 일부만** 바꾼 부칙. 그 상품에 걸리는 것만.

    이런 부칙으로는 버전을 옮기지 않는다(notes/030). 대신 그 사실을 답에
    실어야 한다 — "이 판본이 적용된다"고만 말하면, 그 안에서 조문 몇 개는
    아직 옛 내용이라는 사실이 사라진다.
    """
    with driver.session() as session:
        rows = [dict(record) for record in session.run(
            _ARTICLE_SCOPED_PROVISIONS, version=version
        )]

    notes: list[str] = []
    for row in rows:
        # 적용 단위와 그 단위의 상품이 같은 순서로 들어 있다.
        for note, joined in zip(row["notes"], row["products"], strict=False):
            names = tuple(name for name in joined.split(",") if name)
            # 상품을 한정하지 않은 단위는 모든 상품에 해당한다.
            if names and not any(same_product(name, product) for name in names):
                continue
            notes.append(f"공포 {row['promulgated_on']} — {note}")
    return tuple(notes)


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
                quote=prose_quote(record["text"], QUOTE_CHARS),
                role="coverage",
            )
            for record in records
        )
    _COVERAGE_CACHE[cache_key] = cached
    return cached
