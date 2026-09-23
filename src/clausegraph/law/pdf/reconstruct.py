"""좌표가 있는 줄을 평문으로 되돌린다 — 조문 파서가 XML 경로와 같은 코드를 쓰도록.

## 왜 굳이 평문으로 되돌리나

조문 구조를 읽는 규칙(제N조, ①, 1., 가.)은 이미 `law.terms_parser`에 있고
검증도 끝났다. PDF용으로 같은 규칙을 다시 쓰면 **두 파서가 서로를 채점할 수
없다** — 규칙이 다르면 불일치가 레이아웃 탓인지 규칙 탓인지 가릴 수 없다.

그래서 PDF 쪽은 평문을 만드는 데서 멈추고, 그 뒤는 같은 함수를 부른다.
불일치가 나오면 원인은 하나로 좁혀진다: **레이아웃 복원.**

## 표 줄에 괘선 한 글자를 붙인다

`terms_parser`는 괘선 문자가 있는 줄에서 항·호·목 표기를 읽지 않는다. 표
안의 `1.`은 호가 아니라 표의 내용이기 때문이다. PDF에는 괘선이 문자가 아니라
선으로 그려져 있어 그 규칙이 걸리지 않는다.

표시를 새로 만들지 않고 **같은 괘선 문자를 붙여** 기존 규칙에 태운다. 규칙을
두 벌로 만들지 않기 위한 선택이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import TermsDocument
from ..terms_parser import match_article, parse_terms
from .layout import Line

# `terms_parser._TABLE_CHARS`에 든 문자여야 한다.
TABLE_MARK = "│"

# 조문 머리글의 제목 여는 괄호.
_TITLE_OPEN_RE = re.compile(r"^\s*제\s?\d+\s?조(?:의\s?\d+)?\s*([(\[])")
_TITLE_BRACKETS = {"(": ")", "[": "]"}


@dataclass(frozen=True)
class Reconstructed:
    """평문과, 그 평문의 각 줄이 몇 쪽에서 왔는지."""

    text: str
    pages: tuple[int | None, ...]

    def __post_init__(self) -> None:
        if len(self.text.split("\n")) != len(self.pages):
            raise ValueError("줄 수와 쪽 색인의 길이가 다르다")


def reconstruct(lines: list[Line]) -> Reconstructed:
    texts: list[str] = []
    pages: list[int | None] = []
    for line in lines:
        text = f"{TABLE_MARK}{line.text}" if line.in_table else line.text

        # 조문 제목이 줄을 넘어가면 그 줄에서 괄호가 닫히지 않는다.
        # `terms_parser`는 닫히지 않은 머리글을 조문으로 보지 않으므로
        # (추측해서 자르면 제목과 본문이 섞인다) 여기서 도로 이어 준다 —
        # 줄이 접힌 건 조판 사정이지 문서의 구조가 아니다.
        if texts and _has_open_title(texts[-1]):
            texts[-1] = f"{texts[-1]} {text}"
            continue

        texts.append(text)
        pages.append(line.printed_page)
    return Reconstructed(text="\n".join(texts), pages=tuple(pages))


def _has_open_title(text: str) -> bool:
    """조문 머리글인데 제목 괄호가 그 줄에서 닫히지 않았는가."""
    head = _TITLE_OPEN_RE.match(text)
    if head is None:
        return False
    opener = head.group(1)
    closer = _TITLE_BRACKETS[opener]
    depth = 0
    for char in text[head.start(1) :]:
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return False
    return True


def parse_pdf_terms(
    lines: list[Line], effective_on: str, admrul_seq: int
) -> tuple[TermsDocument, Reconstructed]:
    built = reconstruct(lines)
    return parse_terms(built.text, effective_on, admrul_seq), built


def locate_pages(document: TermsDocument, built: Reconstructed) -> dict[str, int]:
    """조문마다 머리글이 찍힌 쪽을 찾는다.

    조문은 문서 순서대로 나오므로 커서를 앞으로만 옮긴다. 파서가 건너뛴
    머리글(목차·예시 안의 표기 등)이 있어도 커서가 밀리지 않도록, 번호와
    제목이 함께 맞는 줄만 받는다.
    """
    text_lines = built.text.split("\n")
    found: dict[str, int] = {}
    cursor = 0
    for article in document.articles:
        for index in range(cursor, len(text_lines)):
            head = match_article(text_lines[index])
            if head is None:
                continue
            number, title, _ = head
            if number != article.number or title != article.title:
                continue
            page = built.pages[index]
            if page is not None:
                found[article.key] = page
            cursor = index + 1
            break
    return found
