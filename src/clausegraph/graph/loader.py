"""파싱된 표준약관을 Neo4j에 적재한다.

한 버전이 조문 450개 남짓이라 UNWIND로 묶어 넣는다. 적재는 멱등이다 —
MERGE로 쓰므로 같은 데이터를 다시 넣어도 그래프가 늘어나지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import Driver

from ..law.exclusion_table import parse_exclusion_table
from ..law.models import Article, TermsDocument
from ..law.parse_cli import is_exclusion
from ..law.table_parser import Lexicon
from .schema import (
    CONSTRAINTS,
    INDEXES,
    OPEN_ENDED,
    article_uid,
    coverage_uid,
    item_uid,
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

# 같은 상품·같은 조문 번호를 시행일자 순으로 이어 붙인다.
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

    return LoadResult(
        versions=1,
        articles=len(article_rows),
        items=len(item_rows),
        exclusions=sum(1 for row in article_rows if row["is_exclusion"]),
        table_items=len(table_rows),
        coverages=len({row["coverage_uid"] for row in table_rows}),
    )


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
