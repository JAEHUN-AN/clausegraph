"""PDF의 표를 행·셀로 푼다.

평문 경로는 괘선 **문자**를 읽어 셀을 가른다(`law.table_parser`). PDF에는
괘선이 선으로 그려져 있어 셀 사각형이 그대로 있다. 가르는 일은 쉬워지고,
대신 **조판이라서 생기는 문제**가 남는다.

## 표가 쪽을 넘어간다

실손 제4조의 면책표는 열몇 쪽에 걸친다. 쪽이 넘어가면 머리글(`보장종목 /
보상하지 않는 사항`)이 다시 찍히고, **보장종목 칸은 빈칸으로 이어진다.**

    쪽 213 | (1)상해급여 | ① 회사는 다음의 사유로 …
    쪽 214 |             | 1. 전문등반(전문적인 등산용구를 …

빈칸을 그대로 두면 그 사유들이 보장종목을 잃는다. 어느 보장에 걸리는
사유인지가 이 표의 전부인데(notes/005) 그걸 버리는 셈이다. 게다가 호
번호가 쪽마다 1번으로 되돌아간다. 그래서 **이어짐은 앞 행에 도로 붙인다.**

## 내용 칸이 세로로 병합된다

3열 표에서 한 내용이 여러 세부 구성항목을 한꺼번에 덮는다. 그 아래 행들은
**이름만 있고 내용이 없다.** 새 사유가 아니라 같은 사유가 걸리는 보장이
하나 더인 것이므로, 이름을 앞 행에 합친다.

## 셀 안에서 줄이 접힌다

`(1)⏎상해급여`처럼 한 낱말이 두 줄로 잘린다. 여기서 공백이 있었는지는
좌표로도 알 수 없다 — 줄 끝의 공백은 그려지지 않으니까. 그래서 평문 경로가
쓰는 사전(`Lexicon`)을 **그대로 가져다 쓴다.** 판단 규칙을 두 벌로 만들면
두 경로를 맞대 볼 수 없다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ..table_parser import Lexicon

# 쪽마다 다시 찍히는 머리글. 이 낱말이 첫 칸에 있으면 내용 행이 아니다.
HEADER_KEYWORDS = ("보장종목", "보장 종목", "구분")

# 이어 받을 수 있는 칸 수. 3열 표(보장종목/세부구성항목/사항)까지만 본다.
MAX_CARRIED_COLUMNS = 2


@dataclass(frozen=True)
class PdfRow:
    """표의 한 행."""

    printed_page: int | None
    cells: tuple[str, ...]

    @property
    def is_header(self) -> bool:
        return bool(self.cells) and any(word in self.cells[0] for word in HEADER_KEYWORDS)


def join_cell(cell: str | None, lexicon: Lexicon) -> str:
    """셀 안에서 접힌 줄을 잇는다. 공백 여부는 사전에 묻는다."""
    if not cell:
        return ""
    joined = ""
    for raw in cell.split("\n"):
        piece = raw.strip()
        if not piece:
            continue
        if not joined:
            joined = piece
            continue
        joined += ("" if lexicon.is_fused(joined, piece) else " ") + piece
    return joined


def iter_rows(path: Path, lexicon: Lexicon, *, pages: range | None = None) -> Iterator[PdfRow]:
    """PDF의 모든 표를 행으로 흘린다. 머리글 행도 그대로 낸다."""
    import pdfplumber

    from .layout import _FOOTER_RE, FOOTER_ZONE_RATIO

    with pdfplumber.open(str(path)) as page_source:
        selected = page_source.pages if pages is None else [page_source.pages[i] for i in pages]
        for page in selected:
            printed = _printed_page(page, _FOOTER_RE, FOOTER_ZONE_RATIO)
            for table in page.find_tables():
                for cells in table.extract():
                    yield PdfRow(
                        printed_page=printed,
                        cells=tuple(join_cell(cell, lexicon) for cell in cells),
                    )
            page.flush_cache()
            page.get_textmap.cache_clear()


def _printed_page(page, footer_re, zone_ratio) -> int | None:
    footer_zone = page.height * zone_ratio
    for item in page.extract_text_lines(layout=False):
        if item["top"] < footer_zone:
            continue
        match = footer_re.match(item["text"].strip())
        if match:
            return int(match.group(1))
    return None


def stitch(rows: list[PdfRow], lexicon: Lexicon) -> list[PdfRow]:
    """쪽을 넘어가며 잘린 행을 도로 하나로 잇는다.

    머리 칸이 비어 있는 행은 새 행이 아니라 **앞 행의 이어짐**이다. 이걸
    따로 두면 호 번호가 쪽마다 1번부터 다시 시작한다 — 평문 경로에는 쪽이
    없어서 ①의 호가 1번부터 끝까지 이어지는데, PDF만 토막 나서 같은 사유가
    다른 번호를 달게 된다.

    머리글 행은 버리되 이어짐은 끊지 않는다. 쪽이 넘어갈 때마다 머리글이
    다시 찍힐 뿐, 표가 새로 시작하는 것이 아니다.
    """
    stitched: list[PdfRow] = []
    for row in rows:
        if row.is_header or len(row.cells) < 2:
            continue
        # 내용은 늘 마지막 칸이다. 2열 표에서 `cells[:2]`로 잡으면 내용까지
        # 머리 칸으로 세어, 이어짐인데도 아닌 것으로 읽는다.
        head = list(row.cells[:-1])[:MAX_CARRIED_COLUMNS]
        if stitched and not any(head):
            stitched[-1] = _append(stitched[-1], row.cells[-1], lexicon)
            continue

        # 내용 칸이 세로로 병합돼 여러 세부 구성항목을 한꺼번에 덮는다.
        # 그 아래 행들은 **이름만 있고 내용이 없다** — 새 사유가 아니라
        # 앞 행에 함께 걸리는 보장이다. 평문 쪽은 칸 구분선이 없어 애초에
        # 한 행으로 읽히므로, 이쪽도 이름을 앞 행에 합친다.
        if stitched and not row.cells[-1]:
            stitched[-1] = _extend_head(stitched[-1], head)
            continue

        stitched.append(PdfRow(printed_page=row.printed_page, cells=row.cells))
    return stitched


def _extend_head(row: PdfRow, head: list[str]) -> PdfRow:
    """앞 행의 이름 칸에 이어지는 이름을 덧붙인다."""
    cells = list(row.cells)
    for index, value in enumerate(head):
        if not value or index >= len(cells) - 1:
            continue
        current = cells[index]
        if value in current.split():
            continue
        cells[index] = f"{current} {value}".strip()
    return PdfRow(printed_page=row.printed_page, cells=tuple(cells))


def _append(row: PdfRow, tail: str, lexicon: Lexicon) -> PdfRow:
    """이어지는 내용을 앞 행의 마지막 칸에 붙인다."""
    body = row.cells[-1]
    if not tail:
        return row
    merged = tail if not body else body + ("" if lexicon.is_fused(body, tail) else " ") + tail
    return PdfRow(printed_page=row.printed_page, cells=(*row.cells[:-1], merged))
