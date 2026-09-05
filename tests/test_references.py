"""조문 간 참조 추출 — DB 없이 도는 부분."""

from __future__ import annotations

import pytest

from clausegraph.law.references import find_references
from clausegraph.law.terms_parser import match_article

# --- 조문 머리글 ---


@pytest.mark.parametrize(
    "line,expected",
    [
        # 소괄호 제목
        ("제3조(보험금의 지급사유) 회사는", ("3", "보험금의 지급사유")),
        # 조의N
        ("제29조의2(위법계약의 해지) ① 계약자는", ("29의2", "위법계약의 해지")),
        # 번호와 제목 사이 공백
        ("제5조 (보험가입금액 한도 등)", ("5", "보험가입금액 한도 등")),
        # 제목 안에 괄호가 겹친다 — 첫 ')'에서 끊으면 제목이 잘린다
        (
            "제26조(보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지) ① 계약자가",
            ("26", "보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지"),
        ),
        # 제목을 대괄호로 묶는다 — 소괄호만 받으면 조문을 통째로 버린다
        (
            "제27조[보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지] ① 계약자가",
            ("27", "보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지"),
        ),
    ],
)
def test_article_header_is_split_correctly(line: str, expected: tuple[str, str]) -> None:
    parsed = match_article(line)

    assert parsed is not None
    assert parsed[:2] == expected


def test_article_header_keeps_the_rest_as_body() -> None:
    parsed = match_article("제3조(보험금의 지급사유) 회사는 보험금을 지급합니다.")

    assert parsed is not None
    assert parsed[2] == "회사는 보험금을 지급합니다."


def test_unclosed_title_is_not_an_article_header() -> None:
    # 짝이 그 줄에서 닫히지 않으면 조문 머리글로 보지 않는다. 추측해서
    # 자르면 제목과 본문이 섞인다.
    assert match_article("제3조(보험금의 지급사유 회사는 지급합니다") is None


def test_a_plain_reference_is_not_an_article_header() -> None:
    assert match_article("계약자는 제20조(계약내용의 변경 등)에 따라") is None


# --- 참조 ---


def test_internal_reference_is_found() -> None:
    refs = find_references("제3조(보장종목별 보상내용)에 따라 보상합니다.")

    assert [r.number for r in refs] == ["3"]


def test_branch_number_is_kept() -> None:
    refs = find_references("제29조의2에 따라 해지할 수 있습니다.")

    assert [r.number for r in refs] == ["29의2"]


@pytest.mark.parametrize(
    "text",
    [
        "「국민건강보험법」 제42조에 따른 요양기관",
        "?금융소비자 보호에 관한 법률? 제47조 및 관련규정",
        "동법 제3조의3에 의한 종합병원",
        "같은 법 제15조에 따라",
        "민법 제768조에 따른 직계혈족",
        "상법 제657조에 따라",
        "보건복지부 고시 제148조",
    ],
)
def test_external_law_references_are_dropped(text: str) -> None:
    # 약관 밖을 가리키는 참조로 엣지를 만들면 번호만 같은 엉뚱한 조문에 이어진다.
    assert find_references(text) == ()


def test_law_name_carries_across_a_list() -> None:
    # 법령 이름은 나열의 첫 항에만 나온다.
    assert find_references("「국민건강보험법」제5조, 제53조, 제54조에 따라") == ()


def test_law_name_carries_across_a_paragraph_list() -> None:
    assert find_references(
        "「국민건강보험 요양급여의 기준에 관한 규칙」 제11조제1항 또는 제13조제1항에 따라"
    ) == ()


def test_table_borders_do_not_hide_the_law_name() -> None:
    # 조문이 표 안에 있으면 법령 이름과 참조 사이에 괘선이 끼어든다.
    text = "「의료급여법 시행령」 ┃ ┃ ┃ 제13조 및 별표1에 따라"

    assert find_references(text) == ()


def test_reference_after_a_proviso_is_marked() -> None:
    text = (
        "산재보험에서 보상받는 의료비. 다만, 본인부담의료비는 "
        "제3조(보장종목별 보상내용)에 따라 보상합니다."
    )

    refs = find_references(text)

    assert [r.number for r in refs] == ["3"]
    assert refs[0].in_proviso is True


def test_reference_without_a_proviso_is_not_marked() -> None:
    refs = find_references("제20조(계약내용의 변경 등) 제1항의 절차에 따라")

    assert refs[0].in_proviso is False


def test_repeated_reference_is_reported_once() -> None:
    refs = find_references("제3조에 따라 … 제3조 제1항 … 제3조 제2항")

    assert [r.number for r in refs] == ["3"]


def test_internal_and_external_references_are_separated() -> None:
    text = (
        "「국민건강보험법」 제42조의 요양기관에서 발생한 의료비는 "
        "제3조(보장종목별 보상내용)에 따라 보상합니다."
    )

    assert [r.number for r in find_references(text)] == ["3"]
