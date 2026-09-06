"""부칙 적용례 파서 테스트.

문구는 실제 보험업감독업무시행세칙 부칙에서 가져왔다.
"""

from __future__ import annotations

from clausegraph.law.appendix import (
    Provision,
    applies_to_enrollment,
    parse_provisions,
    parse_scopes,
)


def _xml(promulgated_on: str, body: str) -> str:
    return (
        f"<부칙공포일자>{promulgated_on}</부칙공포일자>"
        f"<부칙공포번호>9999</부칙공포번호>"
        f"<부칙내용 ><![CDATA[{body}]]></부칙내용>"
    )


def test_provision_mentioning_standard_terms_is_picked_up() -> None:
    xml = _xml(
        "20260506",
        "제2조(적용례) 제1조에도 불구하고 [별표15] 표준약관 개정내용은 "
        "2026년 6월 6일 이후 체결되는 보험계약부터 적용한다.",
    )

    provisions = parse_provisions(xml)

    assert len(provisions) == 1
    assert provisions[0].promulgated_on == "20260506"


def test_provision_not_mentioning_standard_terms_is_ignored() -> None:
    xml = _xml("20260506", "제1조(시행일) 이 세칙은 개정일부터 시행한다.")

    assert parse_provisions(xml) == ()


def test_new_contracts_only_is_detected() -> None:
    xml = _xml(
        "20180302",
        "이 세칙 개정내용 중 [별표 15] 표준약관 개정내용은 2018년 4월 1일부터 "
        "신규로 체결되는 계약부터 적용한다.",
    )

    assert parse_provisions(xml)[0].new_contracts_only is True


def test_plain_application_is_not_marked_new_contracts_only() -> None:
    xml = _xml(
        "20100329",
        "<별표 15> 표준약관의 개정 내용은 2010년 6월 1일부터 시행한다.",
    )

    assert parse_provisions(xml)[0].new_contracts_only is False


def test_both_date_notations_are_read() -> None:
    # `2011. 4. 1.`과 `2011년 4월 1일`이 섞여 있다.
    dotted = _xml("20100129", "별표 15 개정규정은 2011. 4. 1. 이후 신계약부터 적용한다.")
    spelled = _xml("20100129", "별표 15 개정규정은 2011년 4월 1일 이후 신계약부터 적용한다.")

    assert parse_provisions(dotted)[0].candidate_dates == ("2011-04-01",)
    assert parse_provisions(spelled)[0].candidate_dates == ("2011-04-01",)


def test_only_sentences_mentioning_standard_terms_are_kept() -> None:
    # 별표15를 언급하지 않는 문장은 이 약관과 무관하다.
    xml = _xml(
        "20181106",
        "[별표 15]의 개정사항은 2019년 1월 1일 이후 신계약부터 적용한다. "
        "[별표 27]의 개정사항은 2009년 10월 1일부터 적용한다.",
    )

    provision = parse_provisions(xml)[0]

    assert provision.candidate_dates == ("2019-01-01",)


def test_multiple_dates_in_one_sentence_are_kept_as_candidates() -> None:
    # 한 문장에 세칙 시행일과 별표 적용일이 섞여 나온다.
    xml = _xml(
        "20100129",
        "이 세칙은 2010. 4. 1.부터 시행한다. 다만 제5-13조 <별표 15> 표준약관 "
        "제7조의 개정규정은 2011. 4. 1. 이후 신계약부터 적용하되 "
        "2010. 4. 1. 체결분은 제외한다.",
    )

    assert len(parse_provisions(xml)[0].candidate_dates) == 2


# --- 가입일 판정 ---


def test_enrollment_on_or_after_the_date_is_covered() -> None:
    provision = parse_provisions(
        _xml(
            "20260506",
            "[별표15] 표준약관 개정내용은 2026년 6월 6일 이후 "
            "체결되는 보험계약부터 적용한다.",
        )
    )[0]

    assert applies_to_enrollment(provision, "20260606") is True
    assert applies_to_enrollment(provision, "20260701") is True


def test_enrollment_before_the_date_is_not_covered() -> None:
    # 세칙 시행일(5/6)과 약관 적용일(6/6)이 다르다. 5/20 가입자는 옛 약관이다.
    provision = parse_provisions(
        _xml(
            "20260506",
            "[별표15] 표준약관 개정내용은 2026년 6월 6일 이후 "
            "체결되는 보험계약부터 적용한다.",
        )
    )[0]

    assert applies_to_enrollment(provision, "20260520") is False


def test_ambiguous_dates_are_not_decided() -> None:
    # 후보가 여럿이면 어느 것이 기준인지 부칙만 보고는 정할 수 없다.
    provision = parse_provisions(
        _xml(
            "20100129",
            "제5-13조 <별표 15> 표준약관 제7조의 개정규정은 2011. 4. 1. 이후 "
            "신계약부터 적용하되 2010. 4. 1. 체결분은 제외한다.",
        )
    )[0]

    assert len(provision.candidate_dates) == 2
    assert applies_to_enrollment(provision, "20200101") is None


def test_provision_without_new_contract_scope_is_not_decided() -> None:
    provision = parse_provisions(
        _xml("20100329", "<별표 15> 표준약관의 개정 내용은 2010년 6월 1일부터 시행한다.")
    )[0]

    assert applies_to_enrollment(provision, "20200101") is None


# --- 상품 범위 ---


def _provision(body: str, promulgated_on: str = "20260506"):
    return parse_provisions(_xml(promulgated_on, body))[0]


def test_excluded_product_is_read() -> None:
    provision = _provision(
        "[별표15] 표준약관(개인실손의료보험은 제외한다) 개정내용은 "
        "2026년 6월 6일 이후 체결되는 보험계약부터 적용한다."
    )

    assert provision.excluded_products == ("개인실손의료보험",)


def test_included_product_is_read() -> None:
    provision = _provision(
        "별표15 중 보증보험 표준약관(채무이행보증보험 표준약관, 신용보험 표준약관, "
        "신원보증보험 표준약관) 규정은 2012년 1월 1일부터 시행한다.",
        "20110705",
    )

    assert "보증보험" in provision.included_products


def test_excluded_product_is_not_covered() -> None:
    # 이걸 놓치면 정반대로 판정한다. 실손에 적용일을 걸면 안 된다.
    provision = _provision(
        "[별표15] 표준약관(개인실손의료보험은 제외한다) 개정내용은 "
        "2026년 6월 6일 이후 체결되는 보험계약부터 적용한다."
    )

    assert provision.covers_product("기본형 실손의료보험(급여 실손의료비)") is False
    assert provision.covers_product("실손의료보험 특별약관1(중증 비급여 실손의료비)") is False


def test_other_products_are_covered_when_only_one_is_excluded() -> None:
    provision = _provision(
        "[별표15] 표준약관(개인실손의료보험은 제외한다) 개정내용은 "
        "2026년 6월 6일 이후 체결되는 보험계약부터 적용한다."
    )

    assert provision.covers_product("생명보험") is True
    assert provision.covers_product("질병·상해보험(손해보험 회사용)") is True


def test_inclusion_list_covers_only_those_products() -> None:
    provision = _provision(
        "별표15 중 <자동차보험> 면책사항 규정은 2004년 8월 22일부터 적용한다.",
        "20040625",
    )

    assert provision.covers_product("자동차보험") is True
    assert provision.covers_product("생명보험") is False


def test_provision_without_scope_covers_everything() -> None:
    provision = _provision("<별표 15> 표준약관의 개정 내용은 2010년 6월 1일부터 시행한다.")

    assert provision.covers_product("생명보험") is True
    assert provision.covers_product("기본형 실손의료보험(급여 실손의료비)") is True

# --- 적용 단위 (notes/030) ---
#
# 아래 문장은 전부 실제 부칙 원문이다. 지어낸 예시로 고정하면 규칙이
# 데이터가 아니라 예시에 맞춰진다.

_TWO_DATES = (
    "다만 [별표 15]의 <자동차보험> 제9조, 제11조의 개정사항은 2020년 10월 22일부터 "
    "시행하고, [별표 15]의 <자동차보험> 제1조, 제20조, 제27조, <별표1>, <별표2>의 "
    "개정사항은 2020년 11월 10일부터 시행한다."
)
_TWO_PRODUCTS = (
    "다만, 제2-34조는 2010. 1. 29.부터 시행하고 제5-13조 <별표 15> 생명보험 "
    "표준약관 제7조 및 질병ㆍ상해보험 표준약관(손해보험 회사용) 제7조의 "
    "개정규정은 2011. 4. 1. 이후 신계약부터 적용한다."
)
_WHOLE_TERMS = (
    "제2조(적용례) 제1조에도 불구하고 [별표15] 표준약관(개인실손의료보험은 "
    "제외한다) 개정내용은 2026년 6월 6일 이후 체결되는 보험계약부터 적용한다."
)


def test_one_provision_can_carry_two_dates() -> None:
    # 날짜 후보가 여럿인 이유는 대개 조문마다 날짜가 다르기 때문이다.
    scopes = parse_scopes(_TWO_DATES)

    assert [s.applies_on for s in scopes] == ["2020-10-22", "2020-11-10"]
    assert scopes[0].articles == ("9", "11")
    assert scopes[1].articles == ("1", "20", "27")


def test_each_scope_keeps_its_own_product() -> None:
    scopes = parse_scopes(_TWO_DATES)

    assert all(s.products == ("자동차보험",) for s in scopes)


def test_tables_are_scoped_too() -> None:
    # 조문이 아니라 별표만 바뀐 부칙도 그 상품 전체가 바뀐 것은 아니다.
    scopes = parse_scopes(_TWO_DATES)

    assert scopes[1].tables == ("1", "2")
    assert scopes[1].article_scoped is True


def test_the_appendix_own_articles_are_not_terms_articles() -> None:
    # `제2조(적용례)`와 `제1조에도 불구하고`는 부칙 자기 조문이다. 이걸
    # 표준약관 조문으로 읽으면 거의 모든 부칙이 제1·2조를 바꿨다고 말한다.
    scopes = parse_scopes(_WHOLE_TERMS)

    assert len(scopes) == 1
    assert scopes[0].articles == ()
    assert scopes[0].article_scoped is False


def test_sechik_article_is_not_a_terms_article() -> None:
    # `제5-13조`는 별표15를 달고 있는 **세칙** 조항이다.
    scopes = parse_scopes(_TWO_PRODUCTS)

    assert scopes[-1].articles == ("7",)


def test_all_products_in_one_segment_are_collected() -> None:
    # "생명보험 표준약관 제7조 및 질병ㆍ상해보험 표준약관 … 제7조"
    scopes = parse_scopes(_TWO_PRODUCTS)

    assert scopes[-1].products == ("생명보험", "질병ㆍ상해보험")


def test_a_segment_without_the_standard_terms_is_a_continuation() -> None:
    # 한 문장에 체결 기준일과 적용 기준일이 함께 오면 뒤 날짜에서 끊긴
    # 조각이 생긴다. 별표15를 가리키지 않으면 새 적용 단위가 아니다.
    text = (
        "② [별표 15]의 개정사항 중 '□ 질병ㆍ상해보험 제16조제6항'을 제외한 "
        "개정사항은 2009년 10월 1일 이후에 신규로 체결된 실손의료보험 계약에 "
        "대해서도 2019년 1월 1일부터 적용한다."
    )

    scopes = parse_scopes(text)

    assert [s.applies_on for s in scopes] == ["2009-10-01"]


# --- 버전을 옮길 수 있는가 ---


def _scoped_provision(text: str) -> Provision:
    """적용 단위 검증용. 위쪽 은 인자가 둘이라 따로 둔다."""
    return parse_provisions(_xml("20200101", text))[0]


def test_article_scoped_provision_cannot_move_the_version() -> None:
    # 조문 일부만 바꾼 부칙으로 버전을 옮기면 나머지 조문까지 끌려간다.
    provision = _scoped_provision(_TWO_DATES)

    assert provision.version_scope is None
    assert len(provision.article_scopes) == 2


def test_whole_terms_provision_moves_the_version() -> None:
    provision = _scoped_provision(_WHOLE_TERMS)

    assert provision.version_scope is not None
    assert provision.version_scope.applies_on == "2026-06-06"
    assert provision.article_scopes == ()


def test_enrollment_is_judged_from_the_version_scope() -> None:
    provision = _scoped_provision(_WHOLE_TERMS)

    assert applies_to_enrollment(provision, "20260701") is True
    assert applies_to_enrollment(provision, "20260501") is False


def test_enrollment_is_not_judged_when_only_articles_changed() -> None:
    provision = _scoped_provision(_TWO_DATES)

    assert applies_to_enrollment(provision, "20210101") is None
