"""KCD 코드 사전 테스트 — 코드표 없이 도는 부분."""

from __future__ import annotations

import pytest

from clausegraph.kcd.index import (
    EXCLUDED_CHAPTERS,
    MAX_CATEGORIES_PER_FULL_KEY,
    MAX_CATEGORIES_PER_TAIL_KEY,
    Disease,
    KcdIndex,
    category_of,
    term_keys,
)


def _index(*rows: tuple[str, str]) -> KcdIndex:
    return KcdIndex([Disease(code=code, name=name, english="", complete=True)
                     for code, name in rows])


# --- 3자리 분류로 묶기 ---


@pytest.mark.parametrize(
    "code,expected",
    [("F32", "F32"), ("F32.1", "F32"), ("F321", "F32"), ("  k02  ", "K02")],
)
def test_subdivision_collapses_to_category(code: str, expected: str) -> None:
    # 약관은 면책을 3자리 범위로 적는다. 판정에 필요한 것은 세분류가 아니다.
    assert category_of(code) == expected


# --- 존재 검증 ---


def test_known_code_exists() -> None:
    index = _index(("K64", "치질"))

    assert index.exists("K64") is True
    assert index.name("K64") == "치질"


def test_subdivision_falls_back_to_category() -> None:
    # 청구서에는 F32.1이 오고 코드표에는 F32가 있다.
    index = _index(("F32", "반응성 우울증의 단일 에피소드"))

    assert index.exists("F32.1") is True
    assert index.name("F32.1") is not None


def test_unknown_code_does_not_exist() -> None:
    index = _index(("K64", "치질"))

    assert index.exists("ZZ99") is False
    assert index.name("ZZ99") is None


def test_existence_check_alone_cannot_catch_a_wrong_chapter() -> None:
    # notes/012에서 LLM이 발목 골절에 L84.0(티눈)을 냈다. 존재하는 코드다.
    # 존재 검증만으로는 이 오류를 막지 못한다 — 이름을 보여 줘야 사람이 안다.
    index = _index(("L84", "티눈"), ("S82", "복사의 골절"))

    assert index.exists("L84.0") is True
    assert index.name("L84.0") == "티눈"


# --- 용어 열쇠 ---


def test_modifier_is_stripped_into_an_extra_key() -> None:
    full, tails = term_keys("상세불명의 급성 편도염")

    assert "상세불명의 급성 편도염" in full
    assert "급성 편도염" in full
    assert "편도염" in tails


def test_short_tail_is_not_indexed() -> None:
    # 2글자 tail은 한국어에서 너무 흔하다 — '수술'·'이상'·'장애'.
    _, tails = term_keys("분만힘의 이상")

    assert tails == []


def test_parenthetical_is_dropped() -> None:
    full, tails = term_keys("추간판 장애에서의 신경근 압박(M50-M51)")

    assert all("(" not in key for key in [*full, *tails])


def test_empty_name_yields_no_keys() -> None:
    assert term_keys("   ") == ([], [])


def test_one_character_key_is_dropped() -> None:
    # '염' 하나로는 수천 개가 걸린다.
    full, tails = term_keys("암")

    assert all(len(key) >= 2 for key in [*full, *tails])


# --- 색인 ---


def test_lookup_finds_the_category_by_name() -> None:
    index = _index(("K64", "치질"))

    assert index.lookup("치질 수술을 받았습니다") == ("K64",)


def test_everyday_word_is_translated_before_lookup() -> None:
    # 청구서의 '충치'는 코드표에 없다. 공식 표기는 '치아우식'이다.
    index = _index(("K02", "치아우식"))

    assert index.lookup("충치가 심해 임플란트를 했습니다") == ("K02",)


def test_full_key_pointing_at_too_many_categories_is_dropped() -> None:
    rows = [
        (f"A{number:02d}", "흔한염") for number in range(MAX_CATEGORIES_PER_FULL_KEY + 2)
    ]
    index = _index(*rows)

    assert index.lookup("흔한염 진단") == ()


def test_tail_key_limit_is_stricter_than_full_key_limit() -> None:
    # tail은 최대 609개 분류를 가리킬 수 있어 훨씬 엄하게 잡는다.
    assert MAX_CATEGORIES_PER_TAIL_KEY < MAX_CATEGORIES_PER_FULL_KEY


def test_longer_key_suppresses_its_substring() -> None:
    # `복사의 골절`이 맞았는데 `골절`까지 누적하면 M·P·S·T가 섞인다.
    index = _index(("S82", "복사의 골절"), ("M84", "병적 골절"))

    assert index.lookup("복사의 골절 진단") == ("S82",)


def test_non_diagnosis_chapters_are_not_indexed() -> None:
    # Y(외인)·Z(보건서비스 접촉)·U(특수목적)는 상병이 아니다.
    chapter = next(iter(EXCLUDED_CHAPTERS))
    index = _index((f"{chapter}63", "치료 중 용량 착오"))

    assert index.lookup("치료 중 용량 착오가 있었습니다") == ()


def test_narrative_without_any_disease_yields_nothing() -> None:
    index = _index(("K64", "치질"))

    assert index.lookup("보험료를 더 냈으니 돌려주세요") == ()
