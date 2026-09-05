"""색인 단위를 만든다.

두 층위를 함께 넣는다.

- **조문(article)** — 조문 전문. 길면 나눈다.
- **호(item)** — 면책 사유 한 개. 실손은 표에서 뽑은 사유가 여기 온다.

호를 따로 넣는 이유는 면책이 사유 단위로 걸리기 때문이다. 조문 전체를
한 덩어리로 두면 '고의로 자신을 해친 경우' 하나를 묻는 질문이 11,000자짜리
제4조와 겨루게 되고, 그러면 유사도가 묻힌다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..graph.schema import article_uid, item_uid, table_item_uid
from ..law.exclusion_table import parse_exclusion_table
from ..law.models import Article, TermsDocument
from ..law.parse_cli import is_exclusion
from ..law.table_parser import Lexicon

CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
_WHITESPACE_RE = re.compile(r"[ \t]+")
# 괘선은 임베딩에 도움이 안 된다.
_BOX_RE = re.compile(r"[┏┓┗┛┣┫┳┻╋━┃┌┐└┘├┤┬┴┼─│]+")


@dataclass(frozen=True)
class Chunk:
    node_uid: str
    node_kind: str
    effective_from: str
    product: str
    coverage: str | None
    article_number: str
    article_title: str
    is_exclusion: bool
    chunk_index: int
    content: str


def build_chunks(doc: TermsDocument, lexicon: Lexicon | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    for article in doc.articles:
        chunks.extend(_article_chunks(article, doc.effective_on))
        chunks.extend(_item_chunks(article, doc.effective_on))
        if lexicon is not None and is_exclusion(article.title):
            chunks.extend(_table_chunks(article, doc.effective_on, lexicon))
    return chunks


def _article_chunks(article: Article, effective_from: str) -> list[Chunk]:
    uid = article_uid(effective_from, article.unit, article.number)
    # 조문 제목을 본문 앞에 붙인다 — 검색어가 제목 표현을 쓰는 경우가 많다.
    body = f"제{article.number}조({article.title}) {_clean(article.text)}"
    return [
        _chunk(article, effective_from, uid, "article", index, part, coverage=None)
        for index, part in enumerate(split_text(body))
    ]


def _item_chunks(article: Article, effective_from: str) -> list[Chunk]:
    parent = article_uid(effective_from, article.unit, article.number)
    chunks: list[Chunk] = []
    for paragraph in article.paragraphs:
        for item in paragraph.items:
            uid = item_uid(parent, paragraph.number, item.number)
            content = f"{article.unit} 제{article.number}조({article.title}) {_clean(item.text)}"
            chunks.append(
                _chunk(article, effective_from, uid, "item", 0, content, coverage=None)
            )
    return chunks


def _table_chunks(article: Article, effective_from: str, lexicon: Lexicon) -> list[Chunk]:
    parent = article_uid(effective_from, article.unit, article.number)
    chunks: list[Chunk] = []
    for exclusion in parse_exclusion_table(article, lexicon):
        uid = table_item_uid(parent, exclusion.coverage, exclusion.paragraph, exclusion.number)
        content = (
            f"{article.unit} {exclusion.coverage} "
            f"제{article.number}조({article.title}) {_clean(exclusion.text)}"
        )
        chunks.append(
            _chunk(
                article, effective_from, uid, "item", 0, content, coverage=exclusion.coverage
            )
        )
    return chunks


def _chunk(
    article: Article,
    effective_from: str,
    uid: str,
    kind: str,
    index: int,
    content: str,
    *,
    coverage: str | None,
) -> Chunk:
    return Chunk(
        node_uid=uid,
        node_kind=kind,
        effective_from=effective_from,
        product=article.unit,
        coverage=coverage,
        article_number=article.number,
        article_title=article.title,
        is_exclusion=is_exclusion(article.title),
        chunk_index=index,
        content=content,
    )


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """줄 경계를 살린 고정 길이 분할."""
    lines = [line for line in (_clean(line) for line in text.split("\n")) if line]
    if not lines:
        return []

    parts: list[str] = []
    buffer: list[str] = []
    length = 0
    for line in lines:
        if length + len(line) > size and buffer:
            parts.append("\n".join(buffer))
            kept: list[str] = []
            back = 0
            for previous in reversed(buffer):
                if back >= overlap:
                    break
                kept.insert(0, previous)
                back += len(previous)
            buffer, length = kept, back
        buffer.append(line)
        length += len(line)
    if buffer:
        parts.append("\n".join(buffer))
    return parts


def _clean(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", _BOX_RE.sub(" ", text)).strip()
