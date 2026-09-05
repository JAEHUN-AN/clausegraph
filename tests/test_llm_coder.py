"""LLM 출력 검증 테스트 — 서버 없이 도는 부분.

LLM이 무엇을 내놓든 시스템에 들어올 수 있는 것은 KCD 표기뿐이어야 한다.
"""

from __future__ import annotations

import pytest

from clausegraph.llm.coder import MAX_CODES, parse_codes


def test_plain_code_list_is_accepted() -> None:
    codes, dropped = parse_codes("K02, K08")

    assert codes == ("K02", "K08")
    assert dropped == ()


def test_explanation_is_stripped_and_recorded() -> None:
    # 모델이 설명을 붙여도 코드만 남고, 무엇을 버렸는지 기록한다.
    codes, dropped = parse_codes("충치이므로 K02 입니다")

    assert codes == ("K02",)
    assert dropped != ()


def test_none_means_no_code() -> None:
    assert parse_codes("NONE") == ((), ())


def test_lowercase_none_is_also_none() -> None:
    assert parse_codes("none") == ((), ())


def test_empty_response_yields_nothing() -> None:
    assert parse_codes("   ") == ((), ())


def test_subdivision_is_kept() -> None:
    codes, _ = parse_codes("N39.3")

    assert codes == ("N39.3",)


def test_lowercase_code_is_normalized() -> None:
    codes, _ = parse_codes("k08")

    assert codes == ("K08",)


def test_duplicates_collapse() -> None:
    codes, _ = parse_codes("K08, K08, K02")

    assert codes == ("K08", "K02")


@pytest.mark.parametrize("raw", ["보험금을 지급해야 합니다", "지급", "1234", "ABC"])
def test_non_code_text_never_becomes_a_code(raw: str) -> None:
    codes, _ = parse_codes(raw)

    assert codes == ()


def test_code_count_is_capped() -> None:
    # 모델이 코드를 쏟아내면 면책이 과하게 걸린다.
    flood = ", ".join(f"K{index:02d}" for index in range(20))

    codes, _ = parse_codes(flood)

    assert len(codes) == MAX_CODES


def test_limit_can_be_tightened() -> None:
    codes, _ = parse_codes("K01, K02, K03", limit=2)

    assert codes == ("K01", "K02")
