"""부칙 적용례 파서 테스트.

문구는 실제 보험업감독업무시행세칙 부칙에서 가져왔다.
"""

from __future__ import annotations

from clausegraph.law.appendix import applies_to_enrollment, parse_provisions


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
