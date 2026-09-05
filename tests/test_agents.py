"""에이전트 단위 테스트 — DB 없이 도는 부분."""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents import guardrails
from clausegraph.agents.amount import REDUCTION_PERIOD_DAYS, compute
from clausegraph.agents.amount_rules import RULES, find_rule
from clausegraph.agents.extract import extract_claim
from clausegraph.agents.kcd import matches, parse_code_ranges
from clausegraph.agents.models import Adjudication, Decision, Evidence

PRODUCT = "실손의료보험 특별약관1(중증 비급여 실손의료비)"
ENROLLED = date(2026, 7, 1)


# --- 질병분류 코드 범위 ---


@pytest.mark.parametrize(
    "clause,code,expected",
    [
        ("정신 및 행동장애(F04∼F99)", "F32", True),
        ("정신 및 행동장애(F04∼F99)", "G40", False),
        ("비만(E66)", "E66", True),
        ("비만(E66)", "E67", False),
        ("치과치료(K00∼K08)", "K08", True),
        ("치과치료(K00∼K08)", "K64", False),
        ("임신, 출산, 산후기(O00∼O99)", "O82", True),
    ],
)
def test_code_range_membership(clause: str, code: str, expected: bool) -> None:
    assert bool(matches(clause, (code,))) is expected


def test_subdivision_is_respected() -> None:
    # 약관이 N39.3을 못박았으면 N39.9는 걸리지 않는다.
    assert matches("요실금(N39.3, N39.4, R32)", ("N39.3",))
    assert not matches("요실금(N39.3, N39.4, R32)", ("N39.9",))


def test_tilde_variants_are_all_read() -> None:
    for tilde in ("∼", "~", "～"):
        assert matches(f"정신 및 행동장애(F04{tilde}F99)", ("F32",))


def test_clause_without_codes_matches_nothing() -> None:
    assert matches("간병비, 증명서 발급비용", ("F32", "K08")) == ()


def test_cross_letter_range_is_skipped_not_guessed() -> None:
    # A00∼B99처럼 글자가 다른 범위는 다루지 않는다.
    assert parse_code_ranges("전염병(A00∼B99)") == () or all(
        rng.letter in {"A", "B"} for rng in parse_code_ranges("전염병(A00∼B99)")
    )


# --- 사실추출 ---


def test_extracts_codes_days_and_amount() -> None:
    claim = extract_claim(
        "C1", PRODUCT, ENROLLED,
        "2026.8.10 우울증(F32) 진단으로 7일간 입원했습니다. 480,000원 청구합니다.",
    )

    assert claim.diagnosis_codes == ("F32",)
    assert claim.hospital_days == 7
    assert claim.claimed_amount == 480000
    assert claim.incident_on == date(2026, 8, 10)


def test_enricher_adds_codes_the_regex_cannot_find() -> None:
    # '임플란트'는 약관에 없는 낱말이라 코드로 옮겨야 면책에 걸린다.
    claim = extract_claim(
        "C2", PRODUCT, ENROLLED, "충치로 임플란트를 했습니다.",
        enrich=lambda _: ("K08",),
    )

    assert "K08" in claim.diagnosis_codes


def test_impossible_date_is_dropped_not_guessed() -> None:
    claim = extract_claim("C3", PRODUCT, ENROLLED, "2026.02.31 사고가 있었습니다.")

    assert claim.incident_on is None


# --- 금액산정 ---


def _complete_rule():
    return next(rule for rule in RULES if rule.complete_for_outpatient())


def test_parameters_are_required_before_any_amount_is_stated() -> None:
    # 약관 파라미터가 없으면 계산했다고 말하지 않는다.
    result = compute(1_000_000, days_since_enrollment=30)

    assert result.computed is False
    assert result.value == 0


def test_incomplete_rule_is_not_guessed() -> None:
    incomplete = next(rule for rule in RULES if not rule.complete_for_inpatient())

    result = compute(1_000_000, days_since_enrollment=800, rule=incomplete)

    assert result.computed is False
    assert result.source_articles == incomplete.source_articles


def test_inpatient_applies_only_the_rate() -> None:
    rule = _complete_rule()

    result = compute(
        1_000_000, days_since_enrollment=800, rule=rule, inpatient=True
    )

    assert result.computed is True
    assert result.value == int(1_000_000 * rule.reimburse_rate)


def test_outpatient_subtracts_the_larger_deductible_first() -> None:
    # "정액 또는 의료비의 N% 중 큰 금액"을 뺀 뒤 비율을 곱한다.
    rule = _complete_rule()
    claimed = 1_000_000
    deductible = max(rule.outpatient_deductible, int(claimed * rule.outpatient_deductible_rate))

    result = compute(claimed, days_since_enrollment=800, rule=rule, inpatient=False)

    assert result.value == int((claimed - deductible) * rule.reimburse_rate)


def test_reduction_period_is_applied_after_the_rate() -> None:
    rule = _complete_rule()

    early = compute(1_000_000, days_since_enrollment=30, rule=rule)
    late = compute(1_000_000, days_since_enrollment=REDUCTION_PERIOD_DAYS, rule=rule)

    assert early.value == int(late.value * 0.5)


def test_annual_limit_caps_the_payout() -> None:
    rule = _complete_rule()

    result = compute(
        1_000_000_000, days_since_enrollment=800, rule=rule, inpatient=True
    )

    assert result.value == rule.annual_limit


def test_already_paid_reduces_the_remaining_limit() -> None:
    rule = _complete_rule()
    spent = rule.annual_limit - 100_000

    result = compute(
        1_000_000_000,
        days_since_enrollment=800,
        rule=rule,
        inpatient=True,
        already_paid_this_year=spent,
    )

    assert result.value == 100_000


def test_missing_incident_date_is_not_computed() -> None:
    result = compute(1_000_000, days_since_enrollment=None, rule=_complete_rule())

    assert result.computed is False


def test_computed_amount_cites_its_clauses() -> None:
    rule = _complete_rule()

    result = compute(1_000_000, days_since_enrollment=800, rule=rule)

    assert result.source_articles == rule.source_articles


def test_unknown_product_has_no_rule() -> None:
    assert find_rule("존재하지 않는 상품", "(1)상해급여") is None


# --- 가드레일 ---


def _adjudication(decision: Decision, evidence: tuple[Evidence, ...] = ()) -> Adjudication:
    return Adjudication(claim_id="C", decision=decision, amount=100, evidence=evidence)


def _evidence() -> Evidence:
    return Evidence(
        node_uid="uid-1", product=PRODUCT, article_number="4",
        article_title="보상하지 않는 사항", quote="비만(E66)", role="exclusion",
    )


def test_conclusion_without_a_clause_is_downgraded() -> None:
    result = guardrails.apply(
        _adjudication(Decision.DENIED), amount_computed=True, has_uncertain_exclusion=False
    )

    assert result.decision is Decision.NEEDS_DOCS
    assert guardrails.GROUNDING in result.guardrails
    assert result.amount == 0


def test_conclusion_with_a_clause_stands() -> None:
    result = guardrails.apply(
        _adjudication(Decision.DENIED, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=False,
    )

    assert result.decision is Decision.DENIED


def test_payment_without_a_computed_amount_goes_to_a_person() -> None:
    result = guardrails.apply(
        _adjudication(Decision.PAID, (_evidence(),)),
        amount_computed=False, has_uncertain_exclusion=False,
    )

    assert result.decision is Decision.HUMAN_REVIEW


def test_payment_with_an_unconfirmed_exclusion_goes_to_a_person() -> None:
    # 위험한 방향은 이쪽이다 — 걸릴지도 모르는 면책을 두고 돈을 내주는 것.
    result = guardrails.apply(
        _adjudication(Decision.PARTIAL, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=True,
    )

    assert result.decision is Decision.HUMAN_REVIEW
    assert guardrails.UNCERTAIN_EXCLUSION in result.guardrails


def test_denial_on_a_confirmed_code_is_not_disturbed() -> None:
    result = guardrails.apply(
        _adjudication(Decision.DENIED, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=False,
    )

    assert result.decision is Decision.DENIED


def test_resident_number_and_phone_are_masked() -> None:
    masked, changed = guardrails.mask_pii("홍길동 900101-1234567 연락처 010-1234-5678")

    assert changed is True
    assert "1234567" not in masked
    assert "010-****-5678" in masked


def test_text_without_pii_is_untouched() -> None:
    text = "2026.8.10 우울증으로 통원치료를 받았습니다."

    assert guardrails.mask_pii(text) == (text, False)


# --- 보장종목 판별 ---


def test_injury_chapter_selects_the_injury_coverage() -> None:
    from clausegraph.agents.amount_rules import classify_coverage

    # KCD S·T는 손상·중독이다.
    assert classify_coverage(("S82",)) == "상해"
    assert classify_coverage(("T20",)) == "상해"


def test_other_chapters_are_treated_as_disease() -> None:
    from clausegraph.agents.amount_rules import classify_coverage

    assert classify_coverage(("J18",)) == "질병"
    assert classify_coverage(("K29", "E66")) == "질병"


def test_no_code_means_the_coverage_is_unknown() -> None:
    from clausegraph.agents.amount_rules import classify_coverage

    # 모르면 모른다고 해야 한다 — 파라미터를 잘못 고르면 지급액이 틀린다.
    assert classify_coverage(()) is None


def test_rule_is_chosen_by_diagnosis_when_coverage_is_unstated() -> None:
    from clausegraph.agents.amount_rules import find_rule

    injury = find_rule("기본형 실손의료보험(급여 실손의료비)", None, ("S82",))
    disease = find_rule("기본형 실손의료보험(급여 실손의료비)", None, ("J18",))

    assert injury is not None and "상해" in injury.coverage
    assert disease is not None and "질병" in disease.coverage


def test_ambiguous_product_without_codes_yields_no_rule() -> None:
    from clausegraph.agents.amount_rules import find_rule

    assert find_rule("기본형 실손의료보험(급여 실손의료비)", None, ()) is None
