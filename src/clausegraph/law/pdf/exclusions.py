"""표 안의 면책 사유를 두 경로로 뽑아 맞댄다.

조문 본문 대조(`compare`)는 양쪽에서 표 줄을 빼고 쟀다. 그런데 **실손 계열의
면책은 표 안에만 있다**(notes/005). 거기를 빼고 잰 78.6%는 이 프로젝트의
핵심을 비껴간 숫자다. 여기서 그 부분을 잰다.

읽는 규칙은 한 벌을 쓴다 — `law.exclusion_table.exclusions_from_rows`.
다른 것은 **셀을 어떻게 얻었는가** 하나뿐이다.

| | 셀을 가르는 근거 | 줄 잇기 |
|---|---|---|
| 평문 | 괘선 문자 `┃` 위치 | 사전(`Lexicon`) |
| PDF | 선으로 그려진 셀 사각형 | 사전(`Lexicon`) |

## 표가 어느 조문의 것인지는 쪽으로 정한다

PDF에서 표는 쪽에 딸려 나오지 조문에 딸려 나오지 않는다. 조문은 문서 순서
대로 배치되므로, **그 조문이 시작한 쪽부터 다음 조문이 시작한 쪽까지**를
그 조문의 구간으로 본다. 경계 쪽에 두 조문이 함께 있으면 표가 앞 조문 쪽으로
붙는데, 면책표는 조문 하나를 통째로 차지해서 실제로 문제가 되지 않았다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..exclusion_table import exclusions_from_rows
from ..models import TableExclusion, TermsDocument
from ..table_parser import Lexicon
from .layout import Line
from .tables import iter_rows, stitch

_WHITESPACE_RE = re.compile(r"\s+")

# 면책 조문 — `law.parse_cli`와 같은 기준.
EXCLUSION_TITLE_MARKERS = ("보상하지 않는", "지급하지 않는", "보상하지 아니")


def is_exclusion(title: str) -> bool:
    return any(marker in title for marker in EXCLUSION_TITLE_MARKERS)


@dataclass(frozen=True)
class ExclusionReport:
    matched: int
    xml_only: tuple[str, ...]
    pdf_only: tuple[str, ...]

    @property
    def xml_total(self) -> int:
        return self.matched + len(self.xml_only)

    @property
    def recall(self) -> float:
        return self.matched / self.xml_total if self.xml_total else 0.0


def key_of(exclusion: TableExclusion) -> str:
    """공백을 지우고 맞댄다 — 줄바꿈 자리는 조판마다 다르다."""
    parts = (
        _WHITESPACE_RE.sub("", exclusion.coverage),
        str(exclusion.paragraph),
        str(exclusion.number),
        _WHITESPACE_RE.sub("", exclusion.text),
    )
    return "#".join(parts)


def page_index_of(lines: list[Line]) -> dict[int, int]:
    """찍힌 쪽 번호 → PDF 장 번호."""
    index: dict[int, int] = {}
    for line in lines:
        if line.printed_page is not None:
            index.setdefault(line.printed_page, line.page_index)
    return index


def article_spans(document: TermsDocument, pages: dict[str, int]) -> dict[str, tuple[int, int]]:
    """조문 키 → (시작 쪽, 끝 쪽). 끝은 다음 조문이 시작한 쪽이다."""
    ordered = [
        (article.key, pages[article.key]) for article in document.articles if article.key in pages
    ]
    spans: dict[str, tuple[int, int]] = {}
    for order, (key, start) in enumerate(ordered):
        end = ordered[order + 1][1] if order + 1 < len(ordered) else start
        spans[key] = (start, max(start, end))
    return spans


def collect_pdf_exclusions(
    pdf_path: Path,
    lexicon: Lexicon,
    span: tuple[int, int],
    printed_to_index: dict[int, int],
) -> tuple[TableExclusion, ...]:
    first, last = span
    indexes = [printed_to_index[p] for p in range(first, last + 1) if p in printed_to_index]
    if not indexes:
        return ()
    rows = list(iter_rows(pdf_path, lexicon, pages=range(min(indexes), max(indexes) + 1)))
    return exclusions_from_rows(row.cells for row in stitch(rows, lexicon))


def compare_exclusions(
    xml_side: tuple[TableExclusion, ...], pdf_side: tuple[TableExclusion, ...]
) -> ExclusionReport:
    left = {key_of(item) for item in xml_side}
    right = {key_of(item) for item in pdf_side}
    return ExclusionReport(
        matched=len(left & right),
        xml_only=tuple(sorted(left - right)),
        pdf_only=tuple(sorted(right - left)),
    )
