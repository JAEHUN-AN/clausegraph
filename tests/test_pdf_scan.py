"""스캔본 손실 측정 테스트.

OCR 엔진은 부르지 않는다. 엔진이 잘 읽느냐는 이 리포가 책임질 일이 아니고,
여기서 고정할 것은 **상자 단위 결과를 줄로 되돌리는 규칙**과 채점 셈이다.
"""

from __future__ import annotations

from clausegraph.law.pdf.scan import PageScan, count_heads, edit_distance, normalize, read_page


class FakeReader:
    """EasyOCR 대역 — `readtext`가 (상자, 글자, 확신도)를 돌려준다."""

    def __init__(self, boxes):
        self._boxes = boxes

    def readtext(self, image, paragraph=False):  # noqa: ARG002 - 서명만 맞춘다
        return self._boxes


def _box(x0, y0, x1, y1, text):
    return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.99)


def test_edit_distance_counts_single_character_changes() -> None:
    assert edit_distance("회사는", "회사가") == 1
    assert edit_distance("", "회사") == 2
    assert edit_distance("보험금", "보험금") == 0


def test_normalize_drops_every_space() -> None:
    assert normalize("회사는 보험금을\n지급합니다") == "회사는보험금을지급합니다"


def test_cer_is_errors_over_truth_length() -> None:
    scan = PageScan(page_index=0, truth="보험금지급", ocr="보험금지금", heads_truth=0, heads_ocr=0)

    assert scan.cer == 1 / 5


def test_read_page_rebuilds_lines_from_boxes() -> None:
    # 같은 줄인데 상자가 둘로 갈린 경우 — 가로 순서로 이어야 한다.
    reader = FakeReader(
        [
            _box(300, 100, 400, 120, "보장합니다"),
            _box(60, 100, 290, 120, "제1조(목적) 이 계약은"),
            _box(60, 140, 300, 160, "제2조(용어의 정의)"),
        ]
    )

    lines = read_page(image=None, reader=reader)

    assert lines == ["제1조(목적) 이 계약은 보장합니다", "제2조(용어의 정의)"]


def test_count_heads_uses_the_same_rule_as_the_parser() -> None:
    assert count_heads(["제1조(목적) 이 계약은", "1. 보험금", "제2조(정의)"]) == 2
