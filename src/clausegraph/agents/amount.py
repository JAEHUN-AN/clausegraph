"""4. 금액산정 — 결정론 계산기.

**여기에 LLM을 쓰지 않는다.** 환각이 곧 지급액 오류가 되는 자리다.
계산이 틀리면 사람이 돈을 덜 받거나 더 받고, 어느 쪽이든 사고다.

산식은 약관이 정한 순서를 그대로 따른다.

    지급액 = min( (실제부담액 − 공제금액) × 보상비율,  잔여 연간한도 )

- **공제금액**은 "여러 항 중 큰 금액"이다. (1)(2)의 공제는 통원에만 붙지만,
  3대비급여ㆍ비급여 MRI의 공제는 "1회당"이라 입원에도 붙는다.
- **보상비율**은 입원에만 붙는다. 제3조 표의 입원 행은 "…의 70%에 해당하는
  금액"처럼 비율을 명시하지만, 통원 행은 공제금액을 뺀 금액 자체를 보상금액으로
  정한다. 입원은 급여 80%ㆍ특약1 70%ㆍ특약2 50%, 통원은 모두 비율이 없다.
- **연간한도**는 보장종목별로 따로 있고, 이미 지급된 금액을 뺀 잔액이 상한이다.
- **감액기간**은 보장개시 초기의 지급사유에 비율을 곱한다. 다른 요소를
  적용한 **뒤에** 곱한다 — 순서를 바꾸면 한도 판정이 달라진다. 다만
  **표준약관은 감액기간을 정하지 않는다.** 규칙이 값을 주지 않으면 이
  단계는 건너뛴다 — 근거 없이 반을 깎으면 과소지급이다(notes/019).

파라미터가 없으면 **계산했다고 말하지 않는다**(`computed=False`).
가드레일이 `HUMAN_REVIEW`로 넘긴다. 추측한 값으로 지급액을 내는 것이 최악이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .amount_rules import AmountRule

# 급여 통원 공제의 정액이 커지는 의료기관. 순서가 중요하다 — "종합병원"이
# "병원"을 포함하므로 큰 쪽을 먼저 본다.
TERTIARY_INSTITUTIONS = ("상급종합병원", "종합병원", "전문요양기관")


@dataclass(frozen=True)
class Amount:
    value: int
    computed: bool
    basis: str
    # 어떤 조항의 값을 썼는지. 근거 없이 나온 금액은 쓸 수 없다.
    source_articles: tuple[str, ...] = ()
    # 공제를 끝까지 계산할 수 없어 **덜 뺐을 수 있다**는 표시. 그러면 이
    # 금액은 지급액이 아니라 지급액의 상한이다. 지급해도 된다는 뜻이 아니다.
    is_upper_bound: bool = False
    # 상한이 된 이유. 무엇이 없어서 못 정했는지 사람이 알아야 한다.
    missing: tuple[str, ...] = ()


def compute(
    claimed_amount: int,
    days_since_enrollment: int | None,
    *,
    rule: AmountRule | None = None,
    inpatient: bool = True,
    already_paid_this_year: int = 0,
    institution: str | None = None,
    copay_rate: float | None = None,
) -> Amount:
    """청구액과 약관 파라미터로 지급액을 계산한다."""
    if claimed_amount <= 0:
        return Amount(0, computed=False, basis="청구액이 없어 계산할 수 없다")

    if days_since_enrollment is None:
        return Amount(0, computed=False, basis="사고일이 없어 감액기간을 판단할 수 없다")

    if rule is None:
        return Amount(
            0,
            computed=False,
            basis="이 상품·보장종목의 지급 파라미터가 없다 — 심사자 확인이 필요하다",
        )

    ready = (
        rule.complete_for_inpatient() if inpatient else rule.complete_for_outpatient()
    )
    if not ready:
        return Amount(
            0,
            computed=False,
            basis=(
                "약관에서 확인하지 못한 값이 있다 — "
                f"{rule.note or '심사자 확인이 필요하다'}"
            ),
            source_articles=rule.source_articles,
        )

    steps: list[str] = []
    missing: list[str] = []

    # 1. 공제 — 통원에만. 약관은 여러 항 중 **큰 금액**을 빼라고 한다.
    #    항을 하나라도 빼먹으면 공제를 덜 빼게 되고, 그건 곧 과다지급이다.
    deductible = 0
    if not inpatient or rule.deductible_applies_to_inpatient:
        deductible, missing = _outpatient_deductible(
            claimed_amount, rule, institution, copay_rate
        )
        note = "덜 뺐을 수 있다" if missing else "여러 항 중 큰 쪽"
        label = "1회당 공제" if rule.deductible_applies_to_inpatient else "통원 공제"
        steps.append(f"{label} {deductible:,}원({note})")
    payable = max(0, claimed_amount - deductible)

    # 2. 보상비율 — 입원과 통원이 다르다. 통원은 약관이 비율을 걸지 않아
    #    1.0이고, 그때는 굳이 "보상비율 100%"라고 적지 않는다.
    rate = rule.rate_for(inpatient) or 0.0
    payable = int(payable * rate)
    if rate != 1.0:
        steps.append(f"보상비율 {rate:.0%}")

    # 2-1. 통원 1회당 한도 — 연간한도와 별개로 걸린다.
    #      비급여 특약은 "통원 1회당 20만원 이내"를 따로 정한다.
    visit_limit = None if inpatient else rule.outpatient_visit_limit
    if visit_limit is not None and payable > visit_limit:
        steps.append(f"통원 1회 한도 {visit_limit:,}원으로 제한")
        payable = visit_limit

    # 3. 감액기간 — 다른 요소를 적용한 뒤에 곱한다. 규칙이 정하지 않았으면
    #    (표준약관은 정하지 않는다) 건너뛴다.
    period = rule.reduction_period_days
    reduction = rule.reduction_rate
    if period is not None and reduction is not None and days_since_enrollment < period:
        payable = int(payable * reduction)
        steps.append(
            f"가입 후 {days_since_enrollment}일 — 감액기간"
            f"({period}일) 안이라 {reduction:.0%}"
        )

    # 4. 연간한도 — 이미 지급된 금액을 뺀 잔액이 상한이다.
    remaining = max(0, (rule.annual_limit or 0) - already_paid_this_year)
    if payable > remaining:
        steps.append(f"연간한도 잔액 {remaining:,}원으로 제한")
        payable = remaining
    else:
        steps.append(f"연간한도 잔액 {remaining:,}원 이내")

    if missing:
        steps.append(f"{', '.join(missing)}을 확인하지 못해 이 금액은 상한이다")

    return Amount(
        payable,
        computed=True,
        basis=" / ".join(steps),
        source_articles=rule.source_articles,
        is_upper_bound=bool(missing),
        missing=tuple(missing),
    )


def _outpatient_deductible(
    claimed_amount: int,
    rule: AmountRule,
    institution: str | None,
    copay_rate: float | None,
) -> tuple[int, list[str]]:
    """통원 공제와, 확인하지 못해 계산에서 빠진 항의 목록.

    빠진 항이 있으면 공제를 **덜** 계산한 것이므로 지급액은 상한이 된다.
    모르는 값을 지어내 공제를 키우면 과소지급이고, 항을 빼먹은 채 지급액이라
    부르면 과다지급이다. 둘 다 하지 않고 상한이라고 말한다.
    """
    missing: list[str] = []

    # 정액 항 — 의료기관 종류로 갈린다. 종류를 모르면 작은 쪽을 쓴다.
    flat = rule.outpatient_deductible or 0
    tertiary = rule.outpatient_deductible_tertiary
    if tertiary is not None:
        if institution is None:
            missing.append("의료기관 종류")
        elif any(name in institution for name in TERTIARY_INSTITUTIONS):
            flat = tertiary

    terms = [flat, int(claimed_amount * (rule.outpatient_deductible_rate or 0))]

    # 본인부담률 항 — 약관에 없는 값이라 청구가 들고 와야 한다.
    if rule.outpatient_deductible_uses_copay_rate:
        if copay_rate is None:
            missing.append("건강보험 본인부담률")
        else:
            terms.append(int(claimed_amount * copay_rate))

    return max(terms), missing
