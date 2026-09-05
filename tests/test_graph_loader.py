"""그래프 적재 행 생성 테스트.

Neo4j 없이 도는 부분만 본다 — 적재 자체는 verify_cli로 확인한다.
"""

from __future__ import annotations

from clausegraph.graph.loader import _article_row, _chunks, _item_rows
from clausegraph.graph.schema import article_uid, item_uid
from clausegraph.law.models import Article, Item, Paragraph

EFFECTIVE_ON = "20260910"


def _article(unit: str, number: str, title: str, paragraphs=()) -> Article:
    return Article(
        section="손해보험",
        subsection=unit if unit != "손해보험" else None,
        chapter=None,
        number=number,
        title=title,
        text="본문",
        paragraphs=paragraphs,
        revised_on=("2014-12-26",),
    )


def test_article_uid_includes_version_and_product() -> None:
    # 같은 '제5조'라도 시행일자와 상품이 다르면 다른 조문이다.
    assert article_uid("20260910", "화재보험", "4") != article_uid("20260910", "자동차보험", "4")
    assert article_uid("20250401", "화재보험", "4") != article_uid("20260910", "화재보험", "4")


def test_article_row_marks_exclusion_from_title() -> None:
    row = _article_row(_article("화재보험", "4", "보상하지 않는 손해"), EFFECTIVE_ON)

    assert row["is_exclusion"] is True
    assert row["unit"] == "화재보험"
    assert row["effective_from"] == EFFECTIVE_ON


def test_ordinary_article_is_not_an_exclusion() -> None:
    row = _article_row(_article("화재보험", "3", "보험금의 지급"), EFFECTIVE_ON)

    assert row["is_exclusion"] is False


def test_item_rows_carry_paragraph_and_parent() -> None:
    article = _article(
        "생명보험",
        "5",
        "보험금을 지급하지 않는 사유",
        paragraphs=(
            Paragraph(
                number=1,
                text="…",
                items=(Item(number=1, text="고의"), Item(number=2, text="수익자 고의")),
                implicit=True,
            ),
        ),
    )

    rows = _item_rows(article, EFFECTIVE_ON)

    parent = article_uid(EFFECTIVE_ON, "생명보험", "5")
    assert [row["number"] for row in rows] == [1, 2]
    assert all(row["article_uid"] == parent for row in rows)
    assert rows[0]["uid"] == item_uid(parent, 1, 1)


def test_items_from_different_paragraphs_do_not_collide() -> None:
    article = _article(
        "자동차보험",
        "8",
        "보상하지 않는 손해",
        paragraphs=(
            Paragraph(number=1, text="…", items=(Item(number=1, text="가"),)),
            Paragraph(number=2, text="…", items=(Item(number=1, text="나"),)),
        ),
    )

    uids = {row["uid"] for row in _item_rows(article, EFFECTIVE_ON)}

    assert len(uids) == 2


def test_rows_are_chunked_for_unwind() -> None:
    chunks = _chunks([{"n": i} for i in range(1200)])

    assert [len(chunk) for chunk in chunks] == [500, 500, 200]


def test_empty_rows_produce_no_chunks() -> None:
    assert _chunks([]) == []
