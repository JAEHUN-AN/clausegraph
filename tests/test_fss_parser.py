"""파서 테스트 — 실제로 받은 페이지를 픽스처로 쓴다.

합성 HTML로는 HWP 내보내기 특유의 span 파편화를 재현할 수 없어,
2026-09-05에 내려받은 실물을 그대로 넣었다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clausegraph.goldset.parser import (
    extract_body,
    html_to_text,
    parse_list,
    parse_total,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "fss_list_insurance_p2.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def view_html() -> str:
    return (FIXTURES / "fss_view_128.html").read_text(encoding="utf-8")


def test_parses_total_and_page_count_from_insurance_filter(list_html: str) -> None:
    # Arrange / Act
    total, pages = parse_total(list_html)

    # Assert — 권역=보험 필터 기준 (전체 201건 중 보험 160건)
    assert (total, pages) == (160, 16)


def test_parses_ten_rows_per_page(list_html: str) -> None:
    refs = parse_list(list_html)

    assert len(refs) == 10
    assert all(ref.rgnl == "보험" for ref in refs)
    assert all(ref.case_slno > 0 for ref in refs)


def test_row_carries_type_title_and_date(list_html: str) -> None:
    first = parse_list(list_html)[0]

    assert first.case_slno == 197
    assert first.cvpl == "자동차보험(대물)"
    assert first.registered_on == "2026-01-09"
    assert "자동차시세 하락손해" in first.title


def test_rows_are_unique_by_case_slno(list_html: str) -> None:
    refs = parse_list(list_html)

    assert len({ref.case_slno for ref in refs}) == len(refs)


def test_raises_when_list_markup_is_missing() -> None:
    with pytest.raises(ValueError):
        parse_list("<html><body>목록 없음</body></html>")


def test_body_text_reassembles_fragmented_spans(view_html: str) -> None:
    # HWP 내보내기라 이 한 문장이 원본에서는 span 20개 이상으로 쪼개져 있다.
    text = html_to_text(extract_body(view_html))

    assert "A씨는 호흡기질환" in text
    assert "요양병원에 방문하여 한달 간 입원" in text
    assert "<span" not in text
    assert "&nbsp;" not in text


def test_splits_body_into_marker_sections(view_html: str) -> None:
    sections = split_sections(html_to_text(extract_body(view_html)))

    assert "민원내용" in sections
    assert "처리결과" in sections
    assert sections["민원내용"].strip()


def test_sections_are_empty_when_no_marker_present() -> None:
    assert split_sections("마커가 전혀 없는 본문") == {}


def test_raises_when_body_container_is_missing() -> None:
    with pytest.raises(ValueError):
        extract_body("<html><body>본문 없음</body></html>")
