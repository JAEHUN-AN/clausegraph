"""feat/overseas-payout 검토 반영 — 구조화 입력과 해외 계산의 경계.

검토(2026-10-20)가 실측으로 재현한 과다지급 경로를 하나씩 고정한다.

- HIGH-1 명시 진단코드가 형식 검증 없이 들어가 면책 대조를 건너뛰었다
- HIGH-2 재심사가 서술을 새로 받으면 서류에서 온 코드·금액을 버렸다
- MEDIUM-2 LangGraph 구현이 해외 인자를 넘기지 않았다
- MEDIUM-3 0 값이 "모름"이 아니라 값으로 들어갔다
- MEDIUM-4 무엇이 빠졌는지 사유에 이름이 없었다
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from clausegraph.agents import guardrails
from clausegraph.agents.amount import compute
from clausegraph.agents.amount_rules import RULES, find_rule
from clausegraph.agents.extract import InvalidDiagnosisCodeError, extract_claim, parse_codes
from clausegraph.agents.models import Adjudication, Claim, ClaimHistory, Decision, Evidence

OVERSEAS = "기본형 해외여행 실손의료보험"
ENROLLED = date(2026, 7, 1)


# --- HIGH-1: 엄격한 진단코드 ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (("S62.0, F20",), ("S62.0", "F20")),
        (("S62.0 F20",), ("S62.0", "F20")),
        (("S62.0;F20",), ("S62.0", "F20")),
        (("S62.0，F20",), ("S62.0", "F20")),
        (('["S62.0","F20"]',), ("S62.0", "F20")),
        (("S62.0", "F20"), ("S62.0", "F20")),
        # 상병마스터의 점 없는 표기
        (("F200",), ("F20.0",)),
        (("e660",), ("E66.0",)),
        # 세분류는 두 자리까지 — 대조기(agents.kcd)가 두 자리까지만 읽는다
        (("S62120",), ("S62.12",)),
        (("S62.120",), ("S62.12",)),
        # 전부 빈 토큰은 "주지 않았다"
        ((",",), ()),
        ((" ", ""), ()),
    ],
)
def test_parse_codes_accepts_every_separator_and_dotless_form(raw, expected) -> None:
    assert parse_codes(raw) == expected


@pytest.mark.parametrize("raw", [("손목골절",), ("S62.0, 골절",), ("S6",), ("62.0",)])
def test_parse_codes_refuses_anything_that_is_not_a_code(raw) -> None:
    # 버리고 판정하면 버린 토큰이 가린 면책을 건너뛴다.
    with pytest.raises(InvalidDiagnosisCodeError):
        parse_codes(raw)


def test_codes_written_in_the_narrative_are_kept_with_explicit_codes() -> None:
    # 서술의 KCD 코드는 추정이 아니라 정규식으로 읽은 값이다. 버리면 "기왕 진단
    # F20"이 면책에서 빠지고 지급된다.
    claim = extract_claim(
        "C", OVERSEAS, ENROLLED, "기왕 진단 F20(조현병) 환자가 손목 골절",
        enrich=lambda _: ("C34",), diagnosis_codes=("S62.0",),
    )

    assert claim.diagnosis_codes == ("S62.0", "F20")
    assert claim.stated_codes == ("S62.0",)


def test_all_empty_explicit_codes_fall_back_to_the_narrative_path() -> None:
    claim = extract_claim(
        "C", OVERSEAS, ENROLLED, "손목 골절", enrich=lambda _: ("S62",), diagnosis_codes=(",",),
    )

    assert claim.diagnosis_codes == ("S62",)
    assert claim.stated_codes == ()


# --- MEDIUM-3: 0 이하는 "주지 않았다" ---


def test_zero_structured_values_mean_not_given() -> None:
    # 가입금액 0원 계약은 없다. OCR의 빈칸이 0으로 들어온 것으로 읽는다.
    claim = extract_claim(
        "C", OVERSEAS, ENROLLED, "진료비 300만원", claimed_amount=0, sum_insured=0,
    )

    assert claim.claimed_amount == 3_000_000
    assert claim.stated_amount is None
    assert claim.sum_insured is None


def test_overseas_zero_sum_insured_is_treated_as_unknown() -> None:
    rule = find_rule(OVERSEAS, None, ("S62.0",), abroad=True)

    result = compute(
        2_500_000, days_since_enrollment=42, rule=rule, inpatient=False,
        history=ClaimHistory(), sum_insured=0,
    )

    assert result.is_upper_bound is True
    assert "보험가입금액(증권)" in result.missing


# --- 해외 계산의 남은 경계 ---


def test_overseas_pays_nothing_once_the_sum_insured_is_used_up() -> None:
    rule = find_rule(OVERSEAS, None, ("S62.0",), abroad=True)

    result = compute(
        2_500_000, days_since_enrollment=42, rule=rule, inpatient=False,
        history=ClaimHistory(paid_this_year=10_000_000), sum_insured=10_000_000,
    )

    assert result.computed is True
    assert result.value == 0
    assert result.is_upper_bound is False


def test_overseas_claim_above_what_is_left_is_capped() -> None:
    rule = find_rule(OVERSEAS, None, ("S62.0",), abroad=True)

    result = compute(
        2_500_000, days_since_enrollment=42, rule=rule, inpatient=False,
        history=ClaimHistory(paid_this_year=9_000_000), sum_insured=10_000_000,
    )

    assert result.value == 1_000_000
    assert "보험가입금액 잔액 1,000,000원으로 제한" in result.basis


def test_policy_limit_flag_does_not_override_a_limit_written_in_the_terms() -> None:
    # 약관이 한도를 적었으면 그 값을 쓴다 — 증권 값은 약관이 비워 둔 때만.
    rule = replace(find_rule(OVERSEAS, None, ("S62.0",), abroad=True), annual_limit=1_000_000)

    result = compute(
        2_500_000, days_since_enrollment=42, rule=rule, inpatient=False,
        history=ClaimHistory(), sum_insured=10_000_000,
    )

    assert result.value == 1_000_000
    assert "연간한도 잔액 1,000,000원으로 제한" in result.basis


def test_abroad_only_rules_are_never_chosen_unless_abroad_is_true() -> None:
    """성질 테스트 — 상품·보장종목·코드·abroad의 모든 조합.

    해외 규칙은 공제 없이 100%를 준다. 국내 치료나 치료 장소를 모르는 청구에
    한 번이라도 골라지면 과다지급이다.
    """
    products = sorted({rule.product for rule in RULES}) + ["없는 상품"]
    coverages = [None, "", "엉뚱한 보장종목", *sorted({rule.coverage for rule in RULES})]
    code_sets = [(), ("S62.0",), ("A05.9",), ("F41.0",), ("S62.0", "J18"), ("Z99",)]

    for product in products:
        for coverage in coverages:
            for codes in code_sets:
                for abroad in (None, False):
                    rule = find_rule(product, coverage, codes, abroad=abroad)
                    assert rule is None or not rule.abroad_only, (product, coverage, codes, abroad)
                # 해외라고 해도 국내 상품의 선택은 바뀌지 않는다.
                if product != OVERSEAS:
                    assert find_rule(product, coverage, codes, abroad=True) == find_rule(
                        product, coverage, codes
                    )


# --- MEDIUM-4: 빠진 값의 이름 ---


def test_upper_bound_reason_names_the_missing_values() -> None:
    evidence = Evidence(
        node_uid="uid-1", product=OVERSEAS, article_number="3",
        article_title="보장종목별 보상내용", quote="…", role="coverage",
    )
    result = guardrails.apply(
        Adjudication(claim_id="C", decision=Decision.PAID, amount=100, evidence=(evidence,)),
        amount_computed=True,
        has_uncertain_exclusion=False,
        amount_is_upper_bound=True,
        missing=("보험가입금액(증권)", "이 계약 기지급액"),
    )

    assert result.decision is Decision.HUMAN_REVIEW
    assert "보험가입금액(증권), 이 계약 기지급액" in result.reason


# --- HIGH-2: 재심사가 서류에서 온 값을 지킨다 ---


def _documented_claim() -> Claim:
    return extract_claim(
        "C9", OVERSEAS, ENROLLED, "해외 병원 통원 치료",
        diagnosis_codes=("S62.0", "F20"), claimed_amount=2_500_000,
        sum_insured=10_000_000, treated_abroad=True,
    )


def test_new_narrative_cannot_erase_codes_from_the_documents() -> None:
    # 진단서 코드(F20, 면책)로 DENIED였던 건이 청구인이 다시 쓴 서술로 PAID가
    # 되던 경로다.
    from clausegraph.mcp_server.server import _revise

    revised = _revise(_documented_claim(), -1, -1, -1, -1, "손목 골절 S62.0 진료비 250만원")

    assert {"S62.0", "F20"} <= set(revised.diagnosis_codes)
    assert revised.stated_codes == ("S62.0", "F20")


def test_new_narrative_cannot_erase_the_documented_amount() -> None:
    # 서술은 원 단위만 읽는다. "진단서 추가 제출"로 청구액이 0원이 되면 안 된다.
    from clausegraph.mcp_server.server import _revise

    revised = _revise(_documented_claim(), -1, -1, -1, -1, "진단서 추가 제출합니다 USD 1,800")

    assert revised.claimed_amount == 2_500_000
    assert revised.sum_insured == 10_000_000
    assert revised.treated_abroad is True


def test_documented_values_can_be_corrected_explicitly() -> None:
    from clausegraph.mcp_server.server import _revise

    revised = _revise(
        _documented_claim(), -1, -1, -1, -1, "",
        diagnosis_codes="S62.0", claimed_amount=3_000_000, sum_insured=5_000_000,
        treated_abroad=False,
    )

    assert revised.diagnosis_codes == ("S62.0",)
    assert revised.claimed_amount == 3_000_000
    assert revised.sum_insured == 5_000_000
    assert revised.treated_abroad is False


def test_revision_with_a_non_code_is_refused() -> None:
    from clausegraph.mcp_server.server import _revise

    with pytest.raises(InvalidDiagnosisCodeError):
        _revise(_documented_claim(), -1, -1, -1, -1, "", diagnosis_codes="손목골절")


def test_unchanged_revision_keeps_the_claim_as_is() -> None:
    # 구조화 값도 서술도 그대로면 다시 추출하지 않는다 — 누적값만 바뀐다.
    from clausegraph.mcp_server.server import _revise

    claim = _documented_claim()
    revised = _revise(claim, 0, -1, -1, -1, "")

    assert revised.diagnosis_codes == claim.diagnosis_codes
    assert revised.history == ClaimHistory(paid_this_year=0)


# --- MEDIUM-2: 두 오케스트레이터가 같은 해외 인자를 넘긴다 ---


def test_graph_amount_node_passes_the_same_overseas_inputs() -> None:
    pytest.importorskip("langgraph", reason="langgraph extra가 없으면 건너뛴다")
    from clausegraph.agents.graph_orchestrator import _amount

    claim = _documented_claim().model_copy(update={"diagnosis_codes": ("S62.0",)})

    paid = _amount({
        "claim": claim.model_copy(update={"history": ClaimHistory()}),
        "uncertain": [], "steps": [], "coverage": (),
    })["draft"]
    assert paid["amount"] == 2_500_000
    assert paid["amount_is_upper_bound"] is False

    unknown = _amount({"claim": claim, "uncertain": [], "steps": [], "coverage": ()})["draft"]
    assert unknown["amount_missing"] == ("이 계약 기지급액",)
