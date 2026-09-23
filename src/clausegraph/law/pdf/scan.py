"""약관이 스캔본으로만 있으면 이 파이프라인이 견디는가.

표준약관은 글자가 살아 있는 PDF라 읽기 쉽다. 그런데 **보험사가 실제로 쥐고
있는 옛 약관은 스캔본인 경우가 많다.** 그때도 조문 파서가 서는지 미리 알아야
하는데, 스캔본을 구해 오지 않아도 잴 방법이 있다.

**있는 PDF를 이미지로 구워서 글자층을 버리고, 그 이미지를 OCR로 되읽는다.**
원래의 글자층이 정답이므로, 라벨을 만들지 않고 글자 오류율과 조문 인식률을
동시에 잰다. 여기서도 방법은 같다 — 같은 문서를 다른 경로로 한 번 더 읽는다.

## 무엇을 재는가

| | |
|---|---|
| CER | 글자 오류율. 공백은 지우고 센다 |
| 조문 머리글 | `제N조(제목)`이 OCR 뒤에도 조문으로 읽히는가 |

공백을 빼고 세는 이유는 뒷단이 공백을 무시하고 맞대기 때문이다(`compare`).
한국어 OCR의 띄어쓰기는 어차피 원문을 따르지 않는다.

## 엔진은 무거워서 따로 둔다

EasyOCR(CPU)은 torch를 끌고 온다. 그래프 적재·심사에는 필요 없으므로
`pdf` 가 아니라 `ocr` 추가 묶음에 넣었고, 이 모듈을 부를 때만 임포트한다.
GPU는 쓰지 않는다 — 이 프로젝트의 전제다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..terms_parser import match_article

_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_DPI = 300
OCR_LANGUAGES = ("ko", "en")


@dataclass(frozen=True)
class PageScan:
    """한 쪽을 구워서 되읽은 결과."""

    page_index: int
    truth: str
    ocr: str
    heads_truth: int
    heads_ocr: int

    @property
    def cer(self) -> float:
        if not self.truth:
            return 0.0
        return edit_distance(self.truth, self.ocr) / len(self.truth)


def normalize(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def edit_distance(left: str, right: str) -> int:
    """레벤슈타인 거리. 쪽 하나가 1천 자 남짓이라 단순 DP로 충분하다."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, lchar in enumerate(left, start=1):
        current = [i]
        for j, rchar in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (lchar != rchar),
                )
            )
        previous = current
    return previous[-1]


def count_heads(lines: list[str]) -> int:
    return sum(1 for line in lines if match_article(line) is not None)


def render_page(pdf_path: Path, page_index: int, dpi: int = DEFAULT_DPI):
    """PDF 한 쪽을 이미지로 굽는다 — 글자층이 여기서 사라진다."""
    import pypdfium2

    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        page = document[page_index]
        return page.render(scale=dpi / 72).to_pil()
    finally:
        document.close()


def read_page(image, reader) -> list[str]:
    """OCR 결과를 **줄 단위로** 되돌린다.

    EasyOCR은 상자 단위로 돌려주므로 그대로 이으면 줄이 뒤섞인다. 상자의
    세로 중심으로 줄을 묶고, 같은 줄 안에서는 가로 위치로 정렬한다 —
    레이아웃 파서가 글자층에 하던 일과 같다.

    `image`는 **엔진이 받는 모양 그대로** 넘긴다. 이미지를 배열로 바꾸는
    일은 `scan_pages`가 한다 — 여기서 하면 이 함수가 numpy에 묶이는데,
    묶을 이유가 없다. 이 함수가 하는 일은 상자를 줄로 되돌리는 것뿐이다.
    """
    boxes = reader.readtext(image, paragraph=False)
    placed: list[tuple[float, float, str]] = []
    for box, text, _confidence in boxes:
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        placed.append((sum(ys) / len(ys), min(xs), text))

    if not placed:
        return []

    line_height = _median_height(boxes)
    placed.sort(key=lambda item: (item[0], item[1]))

    lines: list[list[tuple[float, str]]] = []
    current_y = placed[0][0]
    current: list[tuple[float, str]] = []
    for y, x, text in placed:
        if abs(y - current_y) > line_height / 2:
            lines.append(current)
            current, current_y = [], y
        current.append((x, text))
    lines.append(current)

    return [" ".join(text for _, text in sorted(line)) for line in lines if line]


def _median_height(boxes) -> float:
    heights = sorted(
        max(point[1] for point in box) - min(point[1] for point in box) for box, _, _ in boxes
    )
    return heights[len(heights) // 2] if heights else 1.0


def scan_pages(
    pdf_path: Path, truth_by_page: dict[int, list[str]], *, dpi: int = DEFAULT_DPI
) -> list[PageScan]:
    """고른 쪽마다 이미지로 굽고 OCR로 되읽어 원본과 맞댄다."""
    import easyocr
    import numpy

    reader = easyocr.Reader(list(OCR_LANGUAGES), gpu=False, verbose=False)
    results: list[PageScan] = []
    for page_index, truth_lines in sorted(truth_by_page.items()):
        image = render_page(pdf_path, page_index, dpi=dpi)
        ocr_lines = read_page(numpy.asarray(image), reader)
        results.append(
            PageScan(
                page_index=page_index,
                truth=normalize("\n".join(truth_lines)),
                ocr=normalize("\n".join(ocr_lines)),
                heads_truth=count_heads(truth_lines),
                heads_ocr=count_heads(ocr_lines),
            )
        )
    return results
