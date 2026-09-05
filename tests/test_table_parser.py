"""괘선 표 파서와 공백 복원 테스트.

문구는 실손의료보험 표준약관 제4조에서 가져왔다. 공백 복원은 이 파서가
가장 틀리기 쉬운 지점이라 실제로 틀렸던 경우를 그대로 고정한다.
"""

from __future__ import annotations

from clausegraph.law.exclusion_table import _split_items, _split_paragraphs
from clausegraph.law.table_parser import Lexicon, find_table_blocks, parse_table

TWO_COLUMN = [
    "┏━━━━┳━━━━━━━━┓",
    "┃보장종목┃보상하지 않는 사항  ┃",
    "┣━━━━╋━━━━━━━━┫",
    "┃(1)     ┃  1. 피보험자가 고의로 자신을 해친 경우. 다만, 심신상실┃",
    "┃상해급여┃등으로 자유로운 의사결정을 할 수 없는 경우            ┃",
    "┗━━━━┻━━━━━━━━┛",
]

THREE_COLUMN = [
    "┏━━━━┳━━━━┳━━━━━━━━┓",
    "┃보장종목┃세부    ┃보상하지 않는 사항  ┃",
    "┣━━━━╋━━━━╋━━━━━━━━┫",
    "┃(1)     ┃해외    ┃ ① 회사는 보상하지  ┃",
    "┃상해    ┃        ┃않습니다.           ┃",
    "┗━━━━┻━━━━┻━━━━━━━━┛",
]


def test_finds_table_block_between_borders() -> None:
    text = "앞 문장\n" + "\n".join(TWO_COLUMN) + "\n뒤 문장"

    blocks = find_table_blocks(text)

    assert len(blocks) == 1
    assert blocks[0][0].startswith("┏")
    assert blocks[0][-1].startswith("┗")


def test_text_without_table_yields_no_block() -> None:
    assert find_table_blocks("표가 없는 조문입니다.") == []


def test_parses_header_and_data_rows() -> None:
    rows = parse_table(TWO_COLUMN, Lexicon("심신상실 등으로"))

    assert len(rows) == 2
    assert rows[0].cells[0].strip() == "보장종목"
    assert rows[1].cells[0] == "(1) 상해급여"


def test_three_column_table_keeps_all_columns() -> None:
    rows = parse_table(THREE_COLUMN, Lexicon(""))

    assert len(rows[1].cells) == 3


def test_wrapped_line_is_spaced_when_spaced_form_is_attested() -> None:
    # 셀 폭에 흡수돼 사라진 공백을 문서의 다른 곳을 근거로 되살린다.
    rows = parse_table(TWO_COLUMN, Lexicon("피보험자가 심신상실 등으로 자유로운"))

    assert "심신상실 등으로" in rows[1].cells[1]


def test_wrapped_line_is_fused_when_fused_form_is_attested() -> None:
    lexicon = Lexicon("직계혈족 또는 배우자")
    block = [
        "┏━━┳━━┓",
        "┃(1) ┃가족관계등록상 직┃",
        "┃    ┃계혈족 또는      ┃",
        "┗━━┻━━┛",
    ]

    rows = parse_table(block, lexicon)

    assert "직계혈족" in rows[0].cells[1]


def test_window_probe_recovers_evidence_the_whole_token_misses() -> None:
    # '산후기(O00∼' + 'O99)로'은 통째로는 어디에도 없지만
    # 경계 주변 'O00∼O99)'는 문서에 있다.
    lexicon = Lexicon("산후기(O00∼O99)와 관련한 치료")

    assert lexicon.is_fused("피보험자의 산후기(O00∼", "O99)로 발생한") is True


def test_no_evidence_falls_back_to_spacing() -> None:
    # 붙여서 없는 낱말을 만드는 쪽이 더 나쁘다.
    assert Lexicon("아무 관련 없는 문장").is_fused("앞말", "뒷말") is False


def test_nested_table_lines_are_not_joined_into_one_flow() -> None:
    block = [
        "┏━━┳━━┓",
        "┃(1) ┃┌────┬────┐┃",
        "┃    ┃│구분│금액│┃",
        "┗━━┻━━┛",
    ]

    rows = parse_table(block, Lexicon(""))

    assert "\n" in rows[0].cells[1]


# --- 흐름 속 항·호 분해 ---


def test_items_are_split_only_on_the_expected_next_number() -> None:
    text = "1. 첫째 사유입니다. 2. 둘째 사유입니다. 3. 셋째 사유입니다."

    assert [number for number, _ in _split_items(text)] == [1, 2, 3]


def test_dates_and_citations_are_not_mistaken_for_item_numbers() -> None:
    # 본문에 ’26.5.6. 같은 날짜와 제1호서식 같은 인용이 널려 있다.
    text = "1. ’26.5.6. 이후 체결된 계약은 별지 제1호서식에 따릅니다. 2. 다음 사유"

    items = _split_items(text)

    assert [number for number, _ in items] == [1, 2]
    assert "’26.5.6." in items[0][1]


def test_paragraph_markers_split_the_cell() -> None:
    text = "① 회사는 보상하지 않습니다. 1. 고의 ② 회사는 다음도 보상하지 않습니다. 1. 과실"

    chunks = _split_paragraphs(text)

    assert [number for number, _ in chunks] == [1, 2]


def test_cell_without_paragraph_marker_is_one_paragraph() -> None:
    assert [number for number, _ in _split_paragraphs("1. 고의로 해친 경우")] == [1]
