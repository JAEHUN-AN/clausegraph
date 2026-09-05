"""파싱된 표준약관을 Neo4j에 적재한다.

한 버전이 조문 450개 남짓이라 UNWIND로 묶어 넣는다. 적재는 멱등이다 —
MERGE로 쓰므로 같은 데이터를 다시 넣어도 그래프가 늘어나지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import Driver

from ..law.appendix import Provision
from ..law.exclusion_table import parse_exclusion_table
from ..law.models import Article, TermsDocument
from ..law.parse_cli import is_exclusion
from ..law.references import find_references
from ..law.table_parser import Lexicon
from .schema import (
    CONSTRAINTS,
    INDEXES,
    OPEN_ENDED,
    article_uid,
    coverage_uid,
    item_uid,
    provision_uid,
    table_item_uid,
)

BATCH_SIZE = 500

_MERGE_VERSION = """
MERGE (v:Version {effective_from: $effective_from})
SET v.effective_to = $effective_to,
    v.sha = $sha,
    v.admrul_seqs = $admrul_seqs,
    v.article_count = $article_count
"""

_MERGE_ARTICLES = """
UNWIND $rows AS row
MERGE (a:Article {uid: row.uid})
SET a.unit = row.unit,
    a.section = row.section,
    a.subsection = row.subsection,
    a.chapter = row.chapter,
    a.number = row.number,
    a.title = row.title,
    a.text = row.text,
    a.revised_on = row.revised_on,
    a.effective_from = row.effective_from
FOREACH (_ IN CASE WHEN row.is_exclusion THEN [1] ELSE [] END |
    SET a:Exclusion)
MERGE (p:Product {name: row.unit})
MERGE (a)-[:OF_PRODUCT]->(p)
WITH a, row
MATCH (v:Version {effective_from: row.effective_from})
MERGE (a)-[:IN_VERSION]->(v)
"""

_MERGE_ITEMS = """
UNWIND $rows AS row
MATCH (a:Article {uid: row.article_uid})
MERGE (i:Item {uid: row.uid})
SET i.number = row.number,
    i.paragraph = row.paragraph,
    i.text = row.text
MERGE (a)-[:HAS_ITEM]->(i)
"""

_MERGE_TABLE_EXCLUSIONS = """
UNWIND $rows AS row
MATCH (a:Article {uid: row.article_uid})
MERGE (c:Coverage {uid: row.coverage_uid})
SET c.name = row.coverage
MERGE (a)-[:HAS_COVERAGE]->(c)
MERGE (i:Item {uid: row.uid})
SET i.number = row.number,
    i.paragraph = row.paragraph,
    i.text = row.text,
    i.coverage = row.coverage,
    i.source = 'table'
MERGE (c)-[:EXCLUDES]->(i)
MERGE (a)-[:HAS_ITEM]->(i)
"""

_MERGE_PROVISIONS = """
UNWIND $rows AS row
MATCH (v:Version {effective_from: $effective_from})
MERGE (p:Provision {uid: row.uid})
SET p.promulgated_on = row.promulgated_on,
    p.new_contracts_only = row.new_contracts_only,
    p.candidate_dates = row.candidate_dates,
    p.text = row.text,
    p.included_products = row.included_products,
    p.excluded_products = row.excluded_products,
    p.applies_from = row.applies_from
MERGE (v)-[link:HAS_PROVISION]->(p)
// is_own은 **관계**에 붙인다. Provision 노드는 여러 버전이 공유하므로
// (부칙은 세칙 개정 이력 전체가 딸려 온다) 노드에 붙이면 한 부칙이 모든
// 버전의 '자기 부칙'이 된다 — 실제로 그렇게 만들었다가 모든 버전이 같은
// 적용일을 갖는 버그를 냈다 (notes/016).
SET link.is_own = coalesce(row.is_own, false)
FOREACH (_ IN CASE WHEN row.is_own THEN [1] ELSE [] END |
    SET v.provision_uid = row.uid,
        v.applies_to_new_contracts_only = row.new_contracts_only)
"""

# 같은 상품·같은 조문 번호를 시행일자 순으로 이어 붙인다.
_MERGE_REFERENCES = """
UNWIND $rows AS row
MATCH (source {uid: row.source_uid})
MATCH (target:Article {uid: row.target_uid})
MERGE (source)-[link:REFERS_TO]->(target)
SET link.in_proviso = row.in_proviso
"""

_LINK_ARTICLE_HISTORY = """
MATCH (a:Article)
WITH a.unit AS unit, a.number AS number, a
ORDER BY a.effective_from
WITH unit, number, collect(a) AS articles
UNWIND range(1, size(articles) - 1) AS i
WITH articles[i] AS newer, articles[i - 1] AS older
MERGE (newer)-[:SUPERSEDES]->(older)
"""

_LINK_VERSION_HISTORY = """
MATCH (v:Version)
WITH v ORDER BY v.effective_from
WITH collect(v) AS versions
UNWIND range(1, size(versions) - 1) AS i
WITH versions[i] AS newer, versions[i - 1] AS older
MERGE (newer)-[:SUPERSEDES]->(older)
"""


@dataclass(frozen=True)
class LoadResult:
    versions: int = 0
    articles: int = 0
    items: int = 0
    exclusions: int = 0
    table_items: int = 0
    coverages: int = 0
    references: int = 0
    # 가리키는 조문을 그 판본·상품에서 찾지 못한 참조. 엣지를 만들지 않고
    # 센다 — 대개 표 셀 안의 외부 법령 참조다(notes/022).
    dangling_references: int = 0


def apply_schema(driver: Driver) -> None:
    with driver.session() as session:
        for statement in (*CONSTRAINTS, *INDEXES):
            session.run(statement)


def load_version(
    driver: Driver,
    doc: TermsDocument,
    *,
    effective_to: str | None,
    sha: str,
    admrul_seqs: list[int],
    lexicon: Lexicon | None = None,
) -> LoadResult:
    """한 시행일자의 약관을 적재한다.

    `lexicon`을 주면 실손 계열처럼 **표로 적힌 면책 사유**까지 푼다.
    """
    article_rows = [_article_row(article, doc.effective_on) for article in doc.articles]
    item_rows = [row for article in doc.articles for row in _item_rows(article, doc.effective_on)]
    table_rows = (
        [row for article in doc.articles for row in _table_rows(article, doc.effective_on, lexicon)]
        if lexicon is not None
        else []
    )

    with driver.session() as session:
        session.run(
            _MERGE_VERSION,
            effective_from=doc.effective_on,
            effective_to=effective_to or OPEN_ENDED,
            sha=sha,
            admrul_seqs=admrul_seqs,
            article_count=len(doc.articles),
        )
        for chunk in _chunks(article_rows):
            session.run(_MERGE_ARTICLES, rows=chunk)
        for chunk in _chunks(item_rows):
            session.run(_MERGE_ITEMS, rows=chunk)
        for chunk in _chunks(table_rows):
            session.run(_MERGE_TABLE_EXCLUSIONS, rows=chunk)

        reference_rows, dangling = _reference_rows(doc, table_rows)
        for chunk in _chunks(reference_rows):
            session.run(_MERGE_REFERENCES, rows=chunk)

    return LoadResult(
        versions=1,
        articles=len(article_rows),
        items=len(item_rows),
        exclusions=sum(1 for row in article_rows if row["is_exclusion"]),
        table_items=len(table_rows),
        coverages=len({row["coverage_uid"] for row in table_rows}),
        references=len(reference_rows),
        dangling_references=dangling,
    )


def load_provisions(
    driver: Driver,
    effective_from: str,
    provisions: tuple[Provision, ...],
    *,
    own_promulgated_on: str | None = None,
) -> int:
    """부칙 적용례를 그 버전에 붙인다.

    부칙은 세칙의 개정 이력 전체가 딸려 오므로 그 버전 **자신의** 부칙을
    가려내야 한다. `own_promulgated_on`(그 버전을 만든 개정의 발령일자)과
    공포일자가 같은 것이 그것이다.

    자신의 부칙이 신계약 기준이고 날짜 후보가 하나면 그 날짜를
    `applies_from`으로 삼는다 — **세칙 시행일이 아니라 약관 적용일**이다.
    후보가 여럿이면 단정하지 않고 시행일자를 그대로 쓴다.

    **적용일은 Version이 아니라 Provision에 붙인다.** 부칙이 상품을 한정하기
    때문이다 — 2026-05-06 개정은 적용일을 6월 6일로 미루면서 개인실손의료보험을
    빼 두었다. 버전 전체에 걸면 실손 가입자에게 틀린 약관을 들이댄다.
    """
    rows = []
    for provision in provisions:
        is_own = (
            own_promulgated_on is not None
            and provision.promulgated_on == own_promulgated_on
        )
        applies_from = None
        if is_own and provision.new_contracts_only and len(provision.candidate_dates) == 1:
            applies_from = provision.candidate_dates[0].replace("-", "")
        rows.append(
            {
                "uid": provision_uid(provision.promulgated_on),
                "promulgated_on": provision.promulgated_on,
                "new_contracts_only": provision.new_contracts_only,
                "candidate_dates": list(provision.candidate_dates),
                "text": provision.text,
                "is_own": is_own,
                "applies_from": applies_from,
                "included_products": list(provision.included_products),
                "excluded_products": list(provision.excluded_products),
            }
        )
    if not rows:
        return 0
    with driver.session() as session:
        for chunk in _chunks(rows):
            session.run(_MERGE_PROVISIONS, effective_from=effective_from, rows=chunk)
    return len(rows)


def link_history(driver: Driver) -> None:
    """버전 간 계보를 잇는다. 모든 버전을 넣은 뒤 한 번 부른다."""
    with driver.session() as session:
        session.run(_LINK_ARTICLE_HISTORY)
        session.run(_LINK_VERSION_HISTORY)


def _article_row(article: Article, effective_from: str) -> dict[str, Any]:
    return {
        "uid": article_uid(effective_from, article.unit, article.number),
        "unit": article.unit,
        "section": article.section,
        "subsection": article.subsection,
        "chapter": article.chapter,
        "number": article.number,
        "title": article.title,
        "text": article.text,
        "revised_on": list(article.revised_on),
        "effective_from": effective_from,
        "is_exclusion": is_exclusion(article.title),
    }


def _item_rows(article: Article, effective_from: str) -> list[dict[str, Any]]:
    parent = article_uid(effective_from, article.unit, article.number)
    return [
        {
            "uid": item_uid(parent, paragraph.number, item.number),
            "article_uid": parent,
            "paragraph": paragraph.number,
            "number": item.number,
            "text": item.text,
        }
        for paragraph in article.paragraphs
        for item in paragraph.items
    ]


def _reference_rows(
    doc: TermsDocument, table_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """조문 간 참조 엣지의 행과, 대상을 찾지 못한 참조의 수.

    가리키는 조문이 **같은 판본·같은 상품**에 있어야 엣지를 만든다. 번호만
    맞는 다른 상품의 조문에 이으면 조용히 틀린 근거를 낸다 — 같은 '제3조'가
    상품마다 전혀 다른 내용이다.

    **표에서 뽑은 면책 사유도 함께 본다.** 실손 계열은 면책을 보장종목별
    표로 적으므로 그 사유가 `article.paragraphs`에 없다. 처음에 이걸 빼먹어
    면책의 예외 참조가 0개로 나왔다 — 정작 예외가 제일 많은 자리였다.
    """
    numbers: dict[str, set[str]] = {}
    for article in doc.articles:
        numbers.setdefault(article.unit, set()).add(article.number)

    rows: list[dict[str, Any]] = []
    dangling = 0
    for article in doc.articles:
        parent = article_uid(doc.effective_on, article.unit, article.number)
        sources = [(parent, article.text)]
        sources.extend(
            (item_uid(parent, paragraph.number, item.number), item.text)
            for paragraph in article.paragraphs
            for item in paragraph.items
        )
        sources.extend(
            (str(row["uid"]), str(row["text"]))
            for row in table_rows
            if row["article_uid"] == parent
        )
        for source_uid, text in sources:
            for reference in find_references(text):
                # 자기 자신을 가리키는 참조는 엣지로 만들지 않는다.
                if reference.number == article.number:
                    continue
                if reference.number not in numbers[article.unit]:
                    dangling += 1
                    continue
                rows.append(
                    {
                        "source_uid": source_uid,
                        "target_uid": article_uid(
                            doc.effective_on, article.unit, reference.number
                        ),
                        "in_proviso": reference.in_proviso,
                    }
                )
    return rows, dangling


def _table_rows(
    article: Article, effective_from: str, lexicon: Lexicon
) -> list[dict[str, Any]]:
    """표로 적힌 면책 사유. 면책 조문에만 있다."""
    if not is_exclusion(article.title):
        return []
    parent = article_uid(effective_from, article.unit, article.number)
    return [
        {
            "uid": table_item_uid(
                parent, exclusion.coverage, exclusion.paragraph, exclusion.number
            ),
            "article_uid": parent,
            "coverage": exclusion.coverage,
            "coverage_uid": coverage_uid(parent, exclusion.coverage),
            "paragraph": exclusion.paragraph,
            "number": exclusion.number,
            "text": exclusion.text,
        }
        for exclusion in parse_exclusion_table(article, lexicon)
    ]


def _chunks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
