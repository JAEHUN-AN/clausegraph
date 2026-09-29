"""상품명 해석 — 정식 명칭과 다르게 들어온 이름을 어디까지 받아 줄 것인가."""

from __future__ import annotations

from clausegraph.product_names import match_product

KNOWN = (
    "기본형 실손의료보험(급여 실손의료비)",
    "기본형 해외여행 실손의료보험",
    "실손의료보험 특별약관1(중증 비급여 실손의료비)",
    "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
    "질병·상해보험(손해보험 회사용)",
    "해외여행 실손의료보험 특별약관1(중증 비급여 실손의료비)",
    "해외여행 실손의료보험 특별약관2(비중증 비급여 실손의료비)",
)


def test_exact_name_passes_through_untouched() -> None:
    match = match_product("실손의료보험 특별약관1(중증 비급여 실손의료비)", KNOWN)

    assert match.name == "실손의료보험 특별약관1(중증 비급여 실손의료비)"
    assert match.exact


def test_spacing_and_brackets_do_not_matter() -> None:
    match = match_product("질병 상해보험 (손해보험 회사용)", KNOWN)

    assert match.name == "질병·상해보험(손해보험 회사용)"
    assert not match.exact


def test_shortened_name_resolves_when_only_one_product_starts_with_it() -> None:
    # 데모 대본의 입력 문구. 해외여행 특약도 이 문자열을 **안에** 품고 있지만
    # 앞머리가 같은 것은 하나뿐이다 — '해외여행'은 사용자가 생략할 수 있는
    # 말이 아니라 상품을 가르는 말이다.
    match = match_product("실손의료보험 특별약관1(중증 비급여)", KNOWN)

    assert match.name == "실손의료보험 특별약관1(중증 비급여 실손의료비)"
    assert not match.exact


def test_ambiguous_name_is_not_guessed() -> None:
    # 국내·해외 어느 쪽인지 이름에 없다. 고르면 지어내는 것이다.
    match = match_product("특별약관1", KNOWN)

    assert match.name is None
    assert set(match.candidates) == {
        "실손의료보험 특별약관1(중증 비급여 실손의료비)",
        "해외여행 실손의료보험 특별약관1(중증 비급여 실손의료비)",
    }


def test_prefix_candidates_are_preferred_when_several_start_with_it() -> None:
    # '실손의료보험 특별약관'은 1·2 둘 다의 앞머리다. 해외여행 특약까지
    # 늘어놓으면 후보가 흐려진다.
    match = match_product("실손의료보험 특별약관", KNOWN)

    assert match.name is None
    assert match.candidates == (
        "실손의료보험 특별약관1(중증 비급여 실손의료비)",
        "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
    )


def test_unknown_name_has_no_candidates() -> None:
    match = match_product("암보험", KNOWN)

    assert match.name is None
    assert match.candidates == ()


def test_blank_name_matches_nothing() -> None:
    # 공백만 남는 이름이 모든 상품의 부분 문자열이 되어 첫 상품으로
    # 풀리면 안 된다.
    for blank in ("", "  ", "()"):
        match = match_product(blank, KNOWN)
        assert match.name is None
        assert match.candidates == ()
