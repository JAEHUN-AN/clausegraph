"""에이전트 단위 테스트 — DB 없이 도는 부분."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from clausegraph.agents import guardrails
from clausegraph.agents.amount import compute
from clausegraph.agents.amount_rules import RULES, find_rule
from clausegraph.agents.exclusion import _quote, matchable, stem
from clausegraph.agents.extract import extract_claim
from clausegraph.agents.kcd import matches, parse_code_ranges
from clausegraph.agents.models import Adjudication, ClaimHistory, Decision, Evidence
from clausegraph.agents.quote import TABLE_MARKER, prose_quote

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


# --- 어절에서 조사 떼기 ---


@pytest.mark.parametrize(
    "token,expected",
    [
        # 이걸 안 하면 조사가 붙은 어절이 희귀어로 보인다.
        ("대상에", "대상"),
        ("치료에서", "치료"),
        ("가능한", "가능"),
        ("처방된", "처방"),
        ("보상하는", "보상"),
        ("의료비를", "의료비"),
        # 조사가 아닌 끝소리는 건드리지 않는다.
        ("보조기", "보조기"),
        ("성장호르몬제", "성장호르몬제"),
        ("간병비", "간병비"),
    ],
)
def test_particle_is_stripped_only_when_it_is_a_particle(token: str, expected: str) -> None:
    assert stem(token) == expected


@pytest.mark.parametrize("token", ["치과", "결과", "제한", "확인", "비만"])
def test_short_word_is_not_stripped_into_a_fragment(token: str) -> None:
    # 떼고 남은 어간이 두 글자 미만이면 떼지 않는다 — '치과'가 '치'가 되면
    # 아무 데나 걸린다.
    assert stem(token) == token


def test_inflected_forms_collapse_to_one_token() -> None:
    # 같은 말이 조사만 다를 때 한 낱말로 세어야 문서빈도가 맞는다.
    assert len({stem(t) for t in ("대상", "대상에", "대상을", "대상의", "대상은")}) == 1


# --- 괄호 안의 '다만'은 예외다 ---

_DENTAL_CLAUSE = (
    "치과치료(K00∼K08, 다만, 안면부 골절로 발생한 의료비는 치아관련 치료를 "
    "제외하고 보상합니다)ㆍ한방치료(다만, 「의료법」 제2조에 따른 한의사를 제외한 "
    "'의사'의 의료행위에 의해서 발생한 의료비는 보상합니다)"
)


def test_proviso_inside_parentheses_is_dropped_from_matching() -> None:
    # 괄호 안 '다만'에 적힌 말은 보상의 표지다. 그걸로 면책을 짚으면
    # 발목 골절 청구가 치과치료 면책에 걸린다.
    body = matchable(_DENTAL_CLAUSE)

    assert "골절" not in body
    assert "의료법" not in body


def test_both_exclusion_subjects_survive() -> None:
    # 괄호를 통째로 지우면 '한방치료'까지 잃는다. '다만'이 든 괄호만 지운다.
    body = matchable(_DENTAL_CLAUSE)

    assert "치과치료" in body
    assert "한방치료" in body


def test_proviso_outside_parentheses_is_kept() -> None:
    # 괄호 밖의 '다만'은 건드리지 않는다. 청구인이 다투는 말이 거기 있다.
    clause = (
        "산재보험에서 보상받는 의료비. 다만, 본인부담의료비는 "
        "제3조(보장종목별 보상내용)에 따라 보상합니다."
    )

    assert "본인부담의료비" in matchable(clause)


def test_clause_without_a_proviso_is_untouched() -> None:
    clause = "정신 및 행동장애(F04∼F99)"

    assert matchable(clause) == clause


# --- 자기부담 연 200만원 상한 (방향이 반대인 조항) ---


def _benefit_inpatient_rule():
    return next(r for r in RULES if r.self_pay_annual_cap is not None)


def test_self_pay_cap_pays_back_the_excess() -> None:
    # "본인부담금의 20%가 연간 200만원을 초과하는 경우 그 초과금액은 보상합니다"
    rule = _benefit_inpatient_rule()
    claimed = 3_000_000
    self_paid_now = claimed - int(claimed * rule.inpatient_rate)   # 60만원

    result = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=True,
        history=ClaimHistory(self_paid_this_year=1_500_000),
    )

    # 150만 + 60만 = 210만 -> 10만원 초과분을 더 준다.
    over = 1_500_000 + self_paid_now - rule.self_pay_annual_cap
    assert result.value == int(claimed * rule.inpatient_rate) + over


def test_self_pay_cap_already_exhausted_pays_everything() -> None:
    rule = _benefit_inpatient_rule()
    claimed = 3_000_000

    result = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=True,
        history=ClaimHistory(self_paid_this_year=rule.self_pay_annual_cap),
    )

    assert result.value == claimed


def test_self_pay_cap_does_nothing_below_the_cap() -> None:
    rule = _benefit_inpatient_rule()
    claimed = 3_000_000

    result = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=True,
        history=ClaimHistory(self_paid_this_year=0),
    )

    assert result.value == int(claimed * rule.inpatient_rate)
    assert "상한 초과분" not in result.basis


def test_unknown_self_pay_makes_the_amount_a_lower_bound() -> None:
    # 이 조항은 알수록 더 지급한다. 모르면 우리가 덜 계산한 것이다.
    rule = _benefit_inpatient_rule()

    result = compute(
        3_000_000, days_since_enrollment=800, rule=rule, inpatient=True
    )

    assert result.is_lower_bound is True
    assert "올해 자기부담 누적" in result.missing_downward
    assert "청구인이 더 받을 수 있다" in result.basis


def test_self_pay_cap_is_inpatient_only() -> None:
    # 조문이 "입원의 경우"라고 못박았다.
    rule = _benefit_inpatient_rule()

    result = compute(
        3_000_000, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="의원", copay_rate=0.20,
        history=ClaimHistory(self_paid_this_year=5_000_000),
    )

    assert result.is_lower_bound is False
    assert "상한 초과분" not in result.basis


def test_special_terms_have_no_self_pay_cap() -> None:
    # 비급여 특약 제5조에는 이 조항이 없다.
    for rule in RULES:
        if rule.product != _benefit_inpatient_rule().product:
            assert rule.self_pay_annual_cap is None, rule.coverage


# --- 근거 인용 ---

# 실제 항목이다. 뒤에 조문 상호참조가 붙어 있고, 앞머리에 면책의 주어가 있다.
_ITEM_WITH_CROSS_REFERENCE = (
    "산재보험에서 보상받는 의료비. 다만, 본인부담의료비(산재보험 요양급여 "
    "산정기준에 따라 발생한 실제 본인 부담의료비)는 제3조(보장종목별 보상내용) "
    "(2)질병급여 제1항 및 제3항부터 제8항에 따라 보상합니다."
)


def test_quote_keeps_the_subject_of_the_exclusion() -> None:
    # 한때 앞머리를 `^.*?제\d+조\([^)]*\)\s*`로 떼려 했는데, 본문 안의 조문
    # 참조를 제목으로 오인해 그 앞을 다 지웠다. 그러면 부지급의 근거로
    # "...에 따라 보상합니다"만 남는다 - 정반대를 인용하는 것이다(notes/020).
    quote = _quote(_ITEM_WITH_CROSS_REFERENCE)

    assert quote.startswith("산재보험에서 보상받는 의료비")
    assert not quote.startswith("(2)질병급여")
    assert not quote.startswith("에 따라")


def test_quote_is_not_a_fragment() -> None:
    # 인용문이 조사로 시작하면 문장 조각이다. 근거로 쓸 수 없다.
    for text in (_ITEM_WITH_CROSS_REFERENCE, "비만(E66)", "정신 및 행동장애(F04∼F99)"):
        quote = _quote(text)
        assert not quote.startswith(("에 ", "을 ", "를 ", "은 ", "는 ", "의 "))
        assert len(quote) >= min(len(text.strip()), 7)


# 표가 곧 내용인 조문. 앞에서 그냥 자르면 인용 예산이 테두리로 채워진다.
_ARTICLE_WITH_TABLE = (
    "회사가 이 계약의 보험기간 중 보장종목별로 각각 보상하거나 공제하는 내용은 "
    "다음과 같습니다.\n\n┏━━━━┳━━━━━━━━━━━━━┓\n┃보장종목┃보상금액┃"
)


def test_quote_stops_where_the_table_starts() -> None:
    quote = prose_quote(_ARTICLE_WITH_TABLE, 160)

    assert quote.endswith(TABLE_MARKER)
    assert "┏" not in quote
    assert "━" not in quote
    assert quote.startswith("회사가 이 계약의 보험기간 중")


def test_quote_says_there_is_a_table_instead_of_drawing_it() -> None:
    # 표 앞에 알맹이가 없는 조문. 없는 문장을 만들지 않고 표시만 붙인다.
    quote = prose_quote("<표1> 통원항목별 공제금액\n┌─────┬────┐", 160)

    assert quote == "<표1> 통원항목별 공제금액" + TABLE_MARKER


def test_quote_leaves_plain_prose_alone() -> None:
    for text in ("정신 및 행동장애(F04∼F99)", "비만(E66)"):
        assert prose_quote(text, 180) == text


def test_quote_marks_truncation() -> None:
    quote = prose_quote("가" * 300, 60)

    assert len(quote) == 61
    assert quote.endswith("…")


# --- 금액산정 ---


def _complete_rule():
    return next(rule for rule in RULES if rule.complete_for_outpatient())


def test_parameters_are_required_before_any_amount_is_stated() -> None:
    # 약관 파라미터가 없으면 계산했다고 말하지 않는다.
    result = compute(1_000_000, days_since_enrollment=30)

    assert result.computed is False
    assert result.value == 0


def test_incomplete_rule_is_not_guessed() -> None:
    # 지금은 모든 규칙의 값을 약관에서 확인했다. 그래도 확인하지 못한 값이
    # 생기면(새 판본, 새 상품) 계산하지 않아야 한다 — 그 성질을 검증한다.
    incomplete = replace(_complete_rule(), annual_limit=None)
    assert incomplete.complete_for_inpatient() is False

    result = compute(1_000_000, days_since_enrollment=800, rule=incomplete)

    assert result.computed is False
    assert result.source_articles == incomplete.source_articles


def test_outpatient_visit_limit_caps_the_payout() -> None:
    # 비급여 특약은 "통원 1회당 20만원 이내"를 연간한도와 별개로 정한다.
    rule = next(r for r in RULES if r.outpatient_visit_limit is not None)

    result = compute(10_000_000, days_since_enrollment=800, rule=rule, inpatient=False)

    assert result.value == rule.outpatient_visit_limit
    assert "통원 1회 한도" in result.basis


def _benefit_rule():
    """급여 실손 규칙 — 공제가 세 항이고 정액이 기관 종류로 갈린다."""
    return next(r for r in RULES if r.outpatient_deductible_uses_copay_rate)


def test_copay_rate_term_is_included_when_given() -> None:
    # 공제는 "정액, 의료비의 20%, 의료비 x 본인부담률 중 큰 금액"이다.
    # 본인부담률 항을 빼먹으면 공제를 덜 빼고 과다지급이 된다.
    rule = _benefit_rule()

    with_rate = compute(
        500_000, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="상급종합병원", copay_rate=0.60, history=ClaimHistory(),
    )

    assert with_rate.is_upper_bound is False
    # 공제 30만원을 뺀 20만원. 통원 1회 한도(20만원)와 같아 그대로 남는다.
    assert with_rate.value == 200_000


def test_missing_copay_rate_makes_the_amount_an_upper_bound() -> None:
    rule = _benefit_rule()

    # 통원 1회 한도(20만원)에 양쪽이 다 걸리지 않도록 작은 청구로 본다.
    claimed = 300_000
    result = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="상급종합병원",
    )
    with_rate = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="상급종합병원", copay_rate=0.60, history=ClaimHistory(),
    )

    # 계산 자체는 됐지만 공제를 덜 뺐을 수 있으므로 지급액이 아니라 상한이다.
    assert result.computed is True
    assert result.is_upper_bound is True
    assert "건강보험 본인부담률" in result.missing
    # 항을 빼먹은 쪽이 더 크다 — 그래서 상한이다.
    assert result.value > with_rate.value


def test_tertiary_institution_uses_the_larger_flat_deductible() -> None:
    # 정액은 1만원, 상급종합ㆍ종합ㆍ전문요양기관은 2만원. 의료비가 작을 때만
    # 정액 항이 비율 항을 넘어서므로, 작은 청구로 확인한다.
    rule = _benefit_rule()
    claimed = 80_000

    clinic = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="의원", copay_rate=0.20,
    )
    tertiary = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=False,
        institution="상급종합병원", copay_rate=0.20,
    )

    assert clinic.value > tertiary.value
    assert clinic.value == int((claimed - 16_000) * rule.rate_for(inpatient=False))
    assert tertiary.value == int((claimed - 20_000) * rule.rate_for(inpatient=False))


def test_unknown_institution_is_flagged_not_guessed() -> None:
    rule = _benefit_rule()

    result = compute(
        80_000, days_since_enrollment=800, rule=rule, inpatient=False,
        copay_rate=0.20,
    )

    assert result.is_upper_bound is True
    assert "의료기관 종류" in result.missing


def test_special_terms_deductible_needs_no_extra_input() -> None:
    # 비급여 특약의 표는 두 항뿐이다("3만원과 의료비의 30% 중 큰 금액").
    # 청구가 본인부담률을 들고 오지 않아도 끝까지 계산된다.
    rule = next(r for r in RULES if not r.outpatient_deductible_uses_copay_rate)

    result = compute(
        1_000_000, days_since_enrollment=800, rule=rule, inpatient=False,
        history=ClaimHistory(),
    )

    assert result.is_upper_bound is False
    assert result.missing == ()


def test_visit_limit_does_not_apply_to_inpatient() -> None:
    rule = next(r for r in RULES if r.outpatient_visit_limit is not None)

    result = compute(1_000_000, days_since_enrollment=800, rule=rule, inpatient=True)

    assert result.value == int(1_000_000 * rule.rate_for(inpatient=True))
    assert "통원 1회 한도" not in result.basis


def test_inpatient_applies_only_the_rate() -> None:
    rule = _complete_rule()

    result = compute(
        1_000_000, days_since_enrollment=800, rule=rule, inpatient=True
    )

    assert result.computed is True
    assert result.value == int(1_000_000 * rule.rate_for(inpatient=True))


def test_outpatient_subtracts_the_larger_deductible_first() -> None:
    # "정액 또는 의료비의 N% 중 큰 금액"을 뺀 뒤 비율을 곱한다.
    rule = _complete_rule()
    # 통원 1회 한도(20만원)에 가리지 않도록 작은 청구로 본다.
    claimed = 200_000
    deductible = max(rule.outpatient_deductible, int(claimed * rule.outpatient_deductible_rate))

    result = compute(
        claimed, days_since_enrollment=800, rule=rule, inpatient=False,
        copay_rate=0.20, institution="의원", history=ClaimHistory(),
    )

    assert result.value == int((claimed - deductible) * rule.rate_for(inpatient=False))


def test_standard_terms_define_no_reduction_period() -> None:
    # 표준약관에는 보장개시 후 감액기간 규정이 없다. 없는 감액을 걸면
    # 2년 미만 가입자의 지급액이 근거 없이 반이 된다(notes/019).
    assert all(
        rule.reduction_period_days is None and rule.reduction_rate is None
        for rule in RULES
    )

    rule = _complete_rule()
    early = compute(1_000_000, days_since_enrollment=30, rule=rule)
    late = compute(1_000_000, days_since_enrollment=3_000, rule=rule)

    assert early.value == late.value
    assert "감액기간" not in early.basis


def test_reduction_period_is_applied_after_the_rate() -> None:
    # 회사 상품 약관이 감액기간을 정한 경우의 순서를 고정한다. 다른 요소를
    # 적용한 **뒤에** 곱해야 한다 — 순서를 바꾸면 한도 판정이 달라진다.
    rule = replace(_complete_rule(), reduction_period_days=730, reduction_rate=0.5)

    early = compute(1_000_000, days_since_enrollment=30, rule=rule)
    late = compute(1_000_000, days_since_enrollment=730, rule=rule)

    assert early.value == int(late.value * 0.5)
    assert "감액기간(730일) 안이라 50%" in early.basis


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
        history=ClaimHistory(paid_this_year=spent),
    )

    assert result.value == 100_000


def test_unknown_history_makes_the_amount_an_upper_bound() -> None:
    # 누적을 0으로 두면 모든 청구가 그 해 첫 청구가 된다 — 조용한 과다지급이다.
    result = compute(
        1_000_000, days_since_enrollment=800, rule=_complete_rule(), inpatient=True
    )

    assert result.computed is True
    assert result.is_upper_bound is True
    assert "올해 기지급액" in result.missing


def test_exhausted_visit_count_pays_nothing() -> None:
    rule = next(r for r in RULES if r.annual_visit_limit is not None)

    result = compute(
        1_000_000,
        days_since_enrollment=800,
        rule=rule,
        inpatient=False,
        history=ClaimHistory(outpatient_visits_this_year=rule.annual_visit_limit),
    )

    assert result.value == 0
    assert "연간 한도" in result.basis


def test_visit_count_limit_is_not_applied_to_inpatient() -> None:
    rule = next(r for r in RULES if r.annual_visit_limit is not None)

    result = compute(
        1_000_000,
        days_since_enrollment=800,
        rule=rule,
        inpatient=True,
        history=ClaimHistory(outpatient_visits_this_year=999),
    )

    assert result.value > 0


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


def test_payment_of_an_upper_bound_goes_to_a_person() -> None:
    # 계산이 "됐다"고 해도 공제 항이 빠졌으면 그 금액은 상한이다.
    # 상한을 지급액으로 내주면 과다지급이 된다.
    result = guardrails.apply(
        _adjudication(Decision.PARTIAL, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=False,
        amount_is_upper_bound=True,
    )

    assert result.decision is Decision.HUMAN_REVIEW
    assert guardrails.AMOUNT_UPPER_BOUND in result.guardrails
    assert result.amount == 0


def test_payment_of_a_lower_bound_goes_to_a_person() -> None:
    # 방향이 반대인 유일한 가드레일 — 청구인이 덜 받는 위험을 막는다.
    result = guardrails.apply(
        _adjudication(Decision.PARTIAL, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=False,
        amount_is_lower_bound=True,
    )

    assert result.decision is Decision.HUMAN_REVIEW
    assert guardrails.AMOUNT_LOWER_BOUND in result.guardrails
    assert "더 받을 수 있으므로" in result.reason


def test_denial_is_not_disturbed_by_an_upper_bound() -> None:
    # 부지급에는 지급액이 없다. 상한 표시가 부지급을 흔들어서는 안 된다.
    result = guardrails.apply(
        _adjudication(Decision.DENIED, (_evidence(),)),
        amount_computed=True, has_uncertain_exclusion=False,
        amount_is_upper_bound=True,
    )

    assert result.decision is Decision.DENIED
    assert guardrails.AMOUNT_UPPER_BOUND not in result.guardrails


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
