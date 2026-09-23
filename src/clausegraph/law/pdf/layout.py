"""PDF 페이지를 줄 단위로 읽는다 — 좌표가 있는 동안 해야 할 일만 여기서 한다.

조문 구조를 읽는 일은 하지 않는다. 그건 평문에서도 되는 일이고, 이미
`law.terms_parser`가 한다. 여기서는 **평문이 되면 사라지는 것**만 건진다.

## 꼬리말은 버리되 쪽 번호는 남긴다

모든 쪽 아래에 `- 12 -`가 찍혀 있다. 본문에 섞이면 조문 한가운데 숫자가
들어가므로 줄 흐름에서는 빼야 한다. 다만 그 숫자 자체는 버리지 않는다 —
목차가 가리키는 쪽, 인용에 붙일 쪽이 그 숫자다.

**PDF의 몇 번째 장인지와 찍힌 쪽 번호는 다를 수 있다.** 지금 판본은 둘이
우연히 같지만, 표지나 간지가 하나만 들어가도 어긋난다. 찍힌 값을 읽는다.

## 표 안의 줄에 표시를 달아 둔다

표 안에서는 줄바꿈이 문장의 끝이 아니라 셀 폭에서 생긴다. 조문 파서가 표
안의 `1.`을 호로 읽으면 없는 호가 생기므로, 표 영역에 걸친 줄은 표시해 둔다.

## 괘선 표만으로는 모자란다

용어 설명 상자(`【전문금융소비자】…`)는 선이 아니라 **채운 사각형**으로
그려져 있다. 괘선이 없으니 표 탐지에 걸리지 않는데, 평문 쪽에서는 같은
자리가 `┌──┐`로 그려져 조문 본문에서 빠진다. 이걸 놓치면 PDF 쪽 조문에만
용어 설명이 끼어들어 본문이 어긋난다.

선으로 그린 표와 칠해서 만든 상자를 **둘 다** 본문 바깥으로 본다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# `- 12 -`. 앞뒤 공백과 전각 하이픈을 함께 받는다.
_FOOTER_RE = re.compile(r"^\s*[-–—]\s*(\d{1,4})\s*[-–—]\s*$")

# 꼬리말은 아래쪽에만 있다. 본문에 같은 모양의 줄이 있어도 가운데면 본문이다.
FOOTER_ZONE_RATIO = 0.88

# 채운 사각형을 상자로 볼 기준. 글자 하나에 친 음영이나 밑줄은 상자가 아니다.
BOX_MIN_WIDTH_RATIO = 0.3
BOX_MIN_HEIGHT = 15.0

# 쪽 전체를 덮는 배경은 상자가 아니다. 이걸 상자로 보면 그 쪽이 통째로 사라진다.
BOX_MAX_HEIGHT_RATIO = 0.9

# 세로선으로 볼 기울기 허용치(pt).
VERTICAL_TOLERANCE = 1.0


@dataclass(frozen=True)
class Line:
    """PDF에서 읽은 줄 하나."""

    page_index: int
    """0부터 세는 PDF 장 번호."""

    printed_page: int | None
    """쪽 아래에 찍힌 번호. 꼬리말이 없는 장은 None."""

    text: str
    x0: float
    x1: float
    top: float
    in_table: bool = False

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


def _is_inside(line_box: tuple[float, float, float, float], box: tuple[float, ...]) -> bool:
    """줄의 세로 중심이 표 상자 안에 들어오는지.

    가로는 보지 않는다. 표 옆에 본문이 붙는 배치가 이 문서엔 없고, 셀 안의
    줄은 표 상자보다 좁게 잡히기 때문이다.
    """
    _, top, _, bottom = line_box
    center = (top + bottom) / 2
    return box[1] <= center <= box[3]


def _filled_boxes(page) -> list[tuple[float, ...]]:
    """칠해서 만든 상자. 선으로 그린 표는 `find_tables`가 잡는다."""
    boxes: list[tuple[float, ...]] = []
    for rect in page.rects:
        if not rect.get("fill"):
            continue
        width, height = rect["x1"] - rect["x0"], rect["bottom"] - rect["top"]
        if width < page.width * BOX_MIN_WIDTH_RATIO or height < BOX_MIN_HEIGHT:
            continue
        if height > page.height * BOX_MAX_HEIGHT_RATIO:
            continue
        boxes.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"]))
    return boxes


def _ruled_boxes(page) -> list[tuple[float, ...]]:
    """세로선 두 개가 같은 높이 구간을 공유하면 그 사이를 상자로 본다.

    용어 설명 상자 중에는 **글줄마다 밑줄이 깔린** 것이 있다. 가로선이
    글줄 수만큼 생기는데 세로 칸막이는 없어서, 표 탐지가 칸을 못 만들고
    포기한다. 그런데 바깥 테두리는 멀쩡히 그려져 있다 — 왼쪽과 오른쪽
    세로선이 같은 y 구간을 덮는다. 테두리만 보면 상자가 잡힌다.
    """
    columns: dict[tuple[int, int], list[float]] = {}
    for line in page.lines:
        if abs(line["x1"] - line["x0"]) >= VERTICAL_TOLERANCE:
            continue
        height = line["bottom"] - line["top"]
        if height < BOX_MIN_HEIGHT or height > page.height * BOX_MAX_HEIGHT_RATIO:
            continue
        columns.setdefault((round(line["top"]), round(line["bottom"])), []).append(line["x0"])

    boxes: list[tuple[float, ...]] = []
    for (top, bottom), xs in columns.items():
        left, right = min(xs), max(xs)
        if right - left < page.width * BOX_MIN_WIDTH_RATIO:
            continue
        boxes.append((left, float(top), right, float(bottom)))
    return boxes


def _page_lines(page, *, detect_tables: bool) -> tuple[list[Line], int | None]:
    table_boxes: list[tuple[float, ...]] = []
    if detect_tables:
        table_boxes = (
            [tuple(t.bbox) for t in page.find_tables()] + _filled_boxes(page) + _ruled_boxes(page)
        )

    raw = page.extract_text_lines(layout=False)
    printed_page: int | None = None
    footer_zone = page.height * FOOTER_ZONE_RATIO

    lines: list[Line] = []
    for item in raw:
        text = item["text"].strip()
        footer = _FOOTER_RE.match(text)
        if footer and item["top"] >= footer_zone:
            printed_page = int(footer.group(1))
            continue
        box = (item["x0"], item["top"], item["x1"], item["bottom"])
        lines.append(
            Line(
                page_index=page.page_number - 1,
                printed_page=None,  # 꼬리말을 다 읽은 뒤에 채운다
                text=text,
                x0=item["x0"],
                x1=item["x1"],
                top=item["top"],
                in_table=any(_is_inside(box, b) for b in table_boxes),
            )
        )

    if printed_page is not None:
        lines = [
            Line(
                page_index=line.page_index,
                printed_page=printed_page,
                text=line.text,
                x0=line.x0,
                x1=line.x1,
                top=line.top,
                in_table=line.in_table,
            )
            for line in lines
        ]
    return lines, printed_page


def iter_lines(
    path: Path, *, detect_tables: bool = True, max_pages: int | None = None
) -> Iterator[Line]:
    """PDF를 앞에서부터 줄 단위로 흘린다.

    492쪽을 한 번에 메모리에 올릴 이유가 없고, 표 탐지가 쪽마다 꽤 걸려서
    부분 실행(`max_pages`)이 필요하다.
    """
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            lines, _ = _page_lines(page, detect_tables=detect_tables)
            yield from lines
            page.flush_cache()
            page.get_textmap.cache_clear()


def extract_lines(
    path: Path, *, detect_tables: bool = True, max_pages: int | None = None
) -> list[Line]:
    return list(iter_lines(path, detect_tables=detect_tables, max_pages=max_pages))
