"""색인 단위 생성 테스트."""

from __future__ import annotations

from clausegraph.law.models import Article, Item, Paragraph, TermsDocument
from clausegraph.rag.chunks import build_chunks, split_text


def _doc(*articles: Article) -> TermsDocument:
    return TermsDocument(
        effective_on="20260910",
        admrul_seq=1,
        articles=tuple(articles),
        sections=("생명보험",),
    )


def _article(title: str, paragraphs=(), text: str = "본문입니다.") -> Article:
    return Article(
        section="생명보험",
        subsection=None,
        chapter=None,
        number="5",
        title=title,
        text=text,
        paragraphs=paragraphs,
    )


def test_article_chunk_carries_title_for_retrieval() -> None:
    # 검색어가 조문 제목 표현을 쓰는 경우가 많아 본문 앞에 붙인다.
    chunks = build_chunks(_doc(_article("보험금을 지급하지 않는 사유")))

    assert chunks[0].content.startswith("제5조(보험금을 지급하지 않는 사유)")


def test_exclusion_flag_follows_the_title() -> None:
    chunks = build_chunks(_doc(_article("보험금을 지급하지 않는 사유")))

    assert all(chunk.is_exclusion for chunk in chunks)


def test_ordinary_article_is_not_flagged() -> None:
    chunks = build_chunks(_doc(_article("보험금의 지급")))

    assert not any(chunk.is_exclusion for chunk in chunks)


def test_items_become_their_own_chunks() -> None:
    # 면책은 사유 단위로 걸린다. 조문 하나로 뭉치면 유사도가 묻힌다.
    article = _article(
        "보험금을 지급하지 않는 사유",
        paragraphs=(
            Paragraph(
                number=1,
                text="…",
                items=(Item(number=1, text="고의로 자신을 해친 경우"),),
                implicit=True,
            ),
        ),
    )

    chunks = build_chunks(_doc(article))

    items = [chunk for chunk in chunks if chunk.node_kind == "item"]
    assert len(items) == 1
    assert "고의로 자신을 해친 경우" in items[0].content


def test_box_drawing_is_stripped_from_content() -> None:
    chunks = build_chunks(_doc(_article("보상하지 않는 사항", text="┏━━┓\n┃표┃\n┗━━┛")))

    assert "┏" not in chunks[0].content


def test_long_text_is_split_with_overlap() -> None:
    text = "\n".join(f"{index}번째 줄입니다. " * 6 for index in range(40))

    parts = split_text(text, size=200, overlap=50)

    assert len(parts) > 1
    assert all(part.strip() for part in parts)


def test_short_text_stays_one_chunk() -> None:
    assert len(split_text("한 줄짜리 조문입니다.")) == 1


def test_empty_text_yields_no_chunk() -> None:
    assert split_text("   \n \n") == []
