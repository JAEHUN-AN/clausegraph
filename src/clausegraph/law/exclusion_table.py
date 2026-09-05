"""표로 적힌 면책 사유를 호 단위로 뽑는다.

실손 계열 6개 상품의 제4조는 사유를 문장으로 열거하지 않고 보장종목별
표로 적는다(notes/005). 표를 셀로 풀고(table_parser) 나면 셀 하나가 한 줄
흐름이 되므로, 줄머리 규칙 대신 흐름 속에서 항·호 표기를 찾아야 한다.

번호 표기를 정규식으로만 잡으면 오탐이 많다 — 본문에 `’26.5.6.`,
`제1호서식`, `280일` 같은 숫자가 널려 있다. 그래서 **다음에 와야 할 번호**
와 맞는 표기만 인정한다. 1 다음에는 2만 온다.
"""

from __future__ import annotations

import re

from .models import Article, TableExclusion
from .table_parser import Lexicon, find_table_blocks, parse_table

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# 흐름 속 번호 표기 후보. 앞이 숫자나 마침표면 날짜·조문 인용이다.
_ITEM_CANDIDATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.\s")
_HEADER_ROW_KEYWORDS = ("보장종목", "구분")

MIN_ITEM_TEXT = 4


def parse_exclusion_table(article: Article, lexicon: Lexicon) -> tuple[TableExclusion, ...]:
    """면책 조문의 표에서 보장종목별 사유를 뽑는다."""
    exclusions: list[TableExclusion] = []
    for block in find_table_blocks(article.text):
        rows = parse_table(block, lexicon)
        for row in rows:
            if len(row.cells) < 2 or _is_header(row.cells):
                continue
            # 표는 2열(보장종목/사항)일 때도 3열(보장종목/세부구성항목/사항)일
            # 때도 있다. 내용은 늘 마지막 열이고, 앞의 열들이 합쳐서 보장종목이다.
            coverage = " ".join(cell.strip() for cell in row.cells[:-1] if cell.strip())
            for paragraph_no, chunk in _split_paragraphs(row.cells[-1]):
                for number, text in _split_items(chunk):
                    exclusions.append(
                        TableExclusion(
                            coverage=coverage,
                            paragraph=paragraph_no,
                            number=number,
                            text=text,
                        )
                    )
    return tuple(exclusions)


def _is_header(cells: tuple[str, ...]) -> bool:
    return any(keyword in cells[0] for keyword in _HEADER_ROW_KEYWORDS)


def _split_paragraphs(text: str) -> list[tuple[int, str]]:
    """① ② … 로 나눈다. 표기가 없으면 통째로 1항."""
    positions = [(index, char) for index, char in enumerate(text) if char in _CIRCLED]
    if not positions:
        return [(1, text)]

    chunks: list[tuple[int, str]] = []
    for order, (start, char) in enumerate(positions):
        end = positions[order + 1][0] if order + 1 < len(positions) else len(text)
        chunks.append((_CIRCLED.index(char) + 1, text[start + 1 : end].strip()))
    return chunks


def _split_items(text: str) -> list[tuple[int, str]]:
    """호를 나눈다. **다음에 와야 할 번호**와 맞는 표기만 인정한다."""
    starts: list[tuple[int, int, int]] = []  # (시작, 본문시작, 번호)
    expected = 1
    for match in _ITEM_CANDIDATE_RE.finditer(text):
        if int(match.group(1)) != expected:
            continue
        starts.append((match.start(), match.end(), expected))
        expected += 1

    items: list[tuple[int, str]] = []
    for order, (_, body_start, number) in enumerate(starts):
        end = starts[order + 1][0] if order + 1 < len(starts) else len(text)
        body = text[body_start:end].strip()
        if len(body) >= MIN_ITEM_TEXT:
            items.append((number, body))
    return items
