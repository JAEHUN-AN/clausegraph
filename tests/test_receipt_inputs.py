"""진료비 영수증에서 오는 두 값 — 요양기관 종류와 건강보험 본인부담률 (notes/045).

급여 통원 공제는 세 항 중 큰 금액이다: 정액(1만원, 상급종합·종합·전문요양기관은
2만원), 보장대상의료비의 20%, 보장대상의료비 × 본인부담률. 앞의 하나와 뒤의 하나가
영수증에서 온다. MCP에 인자가 없어 상한만 나오던 것을 연다.
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents.amount import compute
from clausegraph.agents.amount_rules import find_rule
from clausegraph.agents.extract import (
    InvalidClaimInputError,
    InvalidCopayRateError,
    InvalidInstitutionError,
    extract_claim,
    parse_copay_rate,
    parse_institution,
)
from clausegraph.agents.models import ClaimHistory

BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
ENROLLED = date(2026, 7, 1)


@pytest.mark.parametrize("raw,expected", [
    ("종합병원", "종합병원"),
    ("종합 병원", "종합병원"),
    ("상급종합병원", "상급종합병원"),
    ("병원급", "병원급"),
    ("보건지소", "보건기관"),
    ("·", None),
    ("[]", None),
    ("의원급·보건기관", "의원"),
    ("의원급ㆍ보건기관", "의원"),
    ("의원", "의원"),
    ("보건소", "보건기관"),
    ("", None),
    ("  ", None),
])
def test_institution_is_read_into_a_known_name(raw, expected) -> None:
    assert parse_institution(raw) == expected


@pytest.mark.parametrize(
    "raw", ["종합", "대학병원", "hospital", "종합벙원", "병원", "[ ]종합병원", "[V]종합병원"]
)
def test_unknown_institution_is_refused_not_guessed(raw) -> None:
    # "종합벙원" 같은 오타를 병원으로 읽으면 공제 정액이 1만원이 되어 과다지급이다.
    with pytest.raises(InvalidInstitutionError):
        parse_institution(raw)


@pytest.mark.parametrize(
    "raw,expected", [(0.5, 0.5), (0.3, 0.3), (1.0, 1.0), (None, None), (-1, None)]
)
def test_copay_rate_is_a_ratio(raw, expected) -> None:
    assert parse_copay_rate(raw) == expected


@pytest.mark.parametrize("raw", [50, 1.5, 30.0, 0, 0.0, float("nan"), float("inf"), True, False])
def test_percent_zero_nan_and_bool_copay_rates_are_refused(raw) -> None:
    # 0은 ① 본인부담금이 0이라는 뜻 — 청구액과 모순이다. 받으면 공제 셋째 항이 사라진다.
    with pytest.raises(InvalidCopayRateError):
        parse_copay_rate(raw)


def test_errors_share_one_base_so_the_server_refuses_them_all() -> None:
    assert issubclass(InvalidInstitutionError, InvalidClaimInputError)
    assert issubclass(InvalidCopayRateError, InvalidClaimInputError)


def test_receipt_values_reach_the_claim() -> None:
    claim = extract_claim(
        "C", BENEFIT, ENROLLED, "2026-08-10 급성 기관지염 통원",
        diagnosis_codes=("J20.9",), claimed_amount=45_000,
        institution="종합 병원", copay_rate=0.5,
    )

    assert claim.institution == "종합병원"
    assert claim.copay_rate == 0.5


def test_scene_two_deductible_is_the_largest_of_three_terms() -> None:
    # 종합병원 외래 45,000원, 본인부담률 50% → max(2만원, 9,000원, 22,500원) = 22,500원
    rule = find_rule(BENEFIT, None, ("J20.9",))

    result = compute(
        45_000, days_since_enrollment=40, rule=rule, inpatient=False,
        institution="종합병원", copay_rate=0.5, history=ClaimHistory(),
    )

    assert result.value == 22_500
    assert result.is_upper_bound is False


def test_without_receipt_values_the_amount_is_only_an_upper_bound() -> None:
    rule = find_rule(BENEFIT, None, ("J20.9",))

    result = compute(
        45_000, days_since_enrollment=40, rule=rule, inpatient=False, history=ClaimHistory(),
    )

    assert result.is_upper_bound is True
    assert set(result.missing) == {"의료기관 종류", "건강보험 본인부담률"}


def test_revision_keeps_receipt_values_through_a_new_narrative() -> None:
    from clausegraph.mcp_server.server import _revise

    claim = extract_claim(
        "C", BENEFIT, ENROLLED, "2026-08-10 급성 기관지염 통원",
        diagnosis_codes=("J20.9",), claimed_amount=45_000,
        institution="종합병원", copay_rate=0.5,
    )

    revised = _revise(claim, -1, -1, -1, -1, "2026-08-10 급성 기관지염으로 다시 통원")
    assert revised.institution == "종합병원" and revised.copay_rate == 0.5

    corrected = _revise(claim, -1, -1, -1, -1, "", institution="의원급", copay_rate=0.3)
    assert corrected.institution == "의원" and corrected.copay_rate == 0.3


def test_revision_with_a_bad_receipt_value_is_refused() -> None:
    from clausegraph.mcp_server.server import _revise

    claim = extract_claim("C", BENEFIT, ENROLLED, "통원", diagnosis_codes=("J20.9",))

    with pytest.raises(InvalidClaimInputError):
        _revise(claim, -1, -1, -1, -1, "", copay_rate=50)


def test_symbol_only_institution_in_a_revision_keeps_the_stored_value() -> None:
    from clausegraph.mcp_server.server import _revise

    claim = extract_claim(
        "C", BENEFIT, ENROLLED, "통원", diagnosis_codes=("J20.9",),
        institution="종합병원", copay_rate=0.5,
    )

    for blank in ("·", "[]", "()", "/"):
        revised = _revise(claim, -1, -1, -1, -1, "", institution=blank)
        assert revised.institution == "종합병원", blank


def test_stored_institution_survives_being_parsed_again() -> None:
    # 재심사는 저장된 값을 다시 파서에 넣는다. 정규화한 값은 다시 넣어도 같아야 한다.
    from clausegraph.agents.extract import INSTITUTIONS

    for canonical in set(INSTITUTIONS.values()):
        assert parse_institution(canonical) == canonical


def test_overseas_treatment_ignores_the_institution_name() -> None:
    claim = extract_claim(
        "C", "기본형 해외여행 실손의료보험", ENROLLED, "해외 통원",
        diagnosis_codes=("S62.0",), treated_abroad=True, institution="Mayo Clinic",
    )

    assert claim.institution is None


def test_overseas_revision_accepts_the_foreign_hospital_name() -> None:
    # 처음 심사가 받은 값을 재심사가 거부하면, 영수증 값을 매번 다시 보내는 쪽은 해외 건을
    # 다시 심사할 수 없다(검토 PR #13 병합 전).
    from clausegraph.mcp_server.server import _revise

    claim = extract_claim(
        "C", "기본형 해외여행 실손의료보험", ENROLLED, "해외 통원",
        diagnosis_codes=("S62.0",), claimed_amount=300_000,
        treated_abroad=True, institution="Mayo Clinic",
    )

    revised = _revise(
        claim, -1, -1, -1, -1, "", claimed_amount=350_000, institution="Mayo Clinic",
    )

    assert revised.stated_amount == 350_000
    assert revised.institution is None


def test_specialty_care_designation_does_not_lift_the_clinic_cap() -> None:
    # 특약2 의원급 입원 한도에서 빠지는 것은 종합병원뿐이다. 전문요양기관은 건강보험법의
    # 지정이지 의료법의 종류가 아니라 병원급일 수 있다 — 한도를 빼고 확정하면 200만원
    # 과다지급이다. 종합병원인지 모르므로 상한으로 둔다.
    from clausegraph.agents.amount_rules import RULES

    rule = next(r for r in RULES if r.inpatient_clinic_cap is not None)

    result = compute(
        10_000_000, days_since_enrollment=800, rule=rule, inpatient=True,
        history=ClaimHistory(), institution="전문요양기관",
    )

    assert result.is_upper_bound is True
    assert any("종합병원" in name for name in result.missing)
    assert "의원급 입원 1회 한도" not in result.basis


def test_specialty_care_designation_below_the_cap_needs_nothing_more() -> None:
    from clausegraph.agents.amount_rules import RULES

    rule = next(r for r in RULES if r.inpatient_clinic_cap is not None)

    result = compute(
        2_000_000, days_since_enrollment=800, rule=rule, inpatient=True,
        history=ClaimHistory(), institution="전문요양기관",
    )

    assert result.is_upper_bound is False
    assert result.value == int(2_000_000 * rule.inpatient_rate)


def _receipt_claim():
    return extract_claim(
        "C", BENEFIT, ENROLLED, "2026-08-10 급성 기관지염 통원",
        diagnosis_codes=("J20.9",), claimed_amount=45_000,
        institution="의원", copay_rate=0.3, history=ClaimHistory(),
    )


@pytest.mark.parametrize("question,fact", [
    ("사실 상급종합병원에서 받은 진료예요. 지급액 얼마예요?", "요양기관 종류"),
    ("종합 병원이었어요. 금액이 왜 이건가요?", "요양기관 종류"),
    ("본인부담률이 60%였어요. 지급액 얼마죠?", "본인부담률"),
    ("본인부담률 0.6이에요 얼마 나와요", "본인부담률"),
    ("본인부담 45%였어요. 지급액 얼마예요?", "본인부담률"),
    ("본인 부담 비율이 45%예요", "본인부담률"),
    ("환자부담 0.45였는데 얼마죠?", "본인부담률"),
    ("상급 종합 병원이었어요", "요양기관 종류"),
    ("내과의원이 아니라 한의원이었어요", "요양기관 종류"),
])
def test_corrected_receipt_values_are_new_facts(question, fact) -> None:
    # 고쳐 말한 영수증 값을 금액 질문으로 읽으면, 앞 판의 더 큰 확정 지급액을 그대로
    # 설명한다(검토 PR #13 병합 전: 의원·0.3의 31,500원 — 상급종합병원이면 25,000원).
    from clausegraph.agents.followup import FollowUp, classify, detect_new_facts

    claim = _receipt_claim()

    assert fact in detect_new_facts(question, claim)
    assert classify(question, claim) is FollowUp.NEW_FACTS


@pytest.mark.parametrize("question", [
    "의원에서 받은 건데 왜 이 금액이죠?",
    "의원급인데 금액이 왜 이건가요?",
    "내과의원에서 받았는데 얼마예요?",
    "본인부담률 30%인데 공제가 왜 이렇게 커요?",
    "본인부담률이 뭐예요?",
    # 이미 아는 값이거나, 기관·본인부담률이 아닌 말 — 공백을 지우면 우연히 이름이 생긴다.
    "금액 차이의 원인이 뭔가요?",
    "병원 급여 항목인데 왜 이 금액이죠?",
    "국회의원이 소개한 건데 왜 이 금액이죠?",
    "병의원 어디서 받든 똑같나요?",
    "본인부담률은 30%인데 보상비율 80%라면서요?",
    "공단부담률 70%면 본인부담률 30% 맞죠?",
    "자기부담률 20%인데 왜 이 금액이죠?",
    "본인부담금 13,500원인데 얼마 나와요?",
])
def test_repeating_known_receipt_values_is_not_a_new_fact(question) -> None:
    from clausegraph.agents.followup import detect_new_facts

    found = detect_new_facts(question, _receipt_claim())

    assert "요양기관 종류" not in found and "본인부담률" not in found


def test_institution_named_in_passing_is_not_new_without_a_receipt_value() -> None:
    # 분쟁 서술에는 "요양병원에 입원했는데"처럼 기관 이름이 지나가듯 나온다. 영수증 값을
    # 받지 않은 청구의 금액은 이미 상한이라, 낡은 확정액을 설명할 일이 없다.
    from clausegraph.agents.followup import detect_new_facts

    claim = extract_claim(
        "C", BENEFIT, ENROLLED, "2026-08-10 급성 기관지염 통원",
        diagnosis_codes=("J20.9",), claimed_amount=45_000, history=ClaimHistory(),
    )

    assert "요양기관 종류" not in detect_new_facts("요양병원에 입원했는데 왜 안 되나요?", claim)


def test_rate_portion_has_no_float_error() -> None:
    # 100,000 × 0.29 는 부동소수로 28,999.999… 이다. 공제가 1원 덜 빠지면 1원 과다지급.
    from clausegraph.agents.amount import _portion

    assert _portion(100_000, 0.29) == 29_000
    assert _portion(100_000, 0.57) == 57_000
    assert _portion(12_348, 0.2) == 2_469  # 원 미만은 절사


@pytest.mark.parametrize("value", [0, 0.0, False, "0", "NaN"])
def test_mcp_layer_refuses_zero_and_nan_copay_rates(monkeypatch, value) -> None:
    # MCP는 인자를 pydantic으로 바꾼 뒤 부른다 — False·"0"은 0.0이 되어 들어온다.
    import asyncio

    from clausegraph.mcp_server import server

    monkeypatch.setattr(server, "_product_names", lambda: ((BENEFIT,), (BENEFIT,)))
    result = asyncio.run(server.mcp.call_tool("adjudicate_claim", {
        "product": BENEFIT, "enrolled_on": "2026-07-01", "narrative": "통원",
        "diagnosis_codes": "J20.9", "claimed_amount_krw": 45_000,
        "institution": "종합병원", "copay_rate": value,
        "paid_this_year": 0, "outpatient_visits_this_year": 0, "self_paid_this_year": 0,
    }))

    text = str(result)
    assert "판정하지 않았다" in text and "본인부담률" in text
