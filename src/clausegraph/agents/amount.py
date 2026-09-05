"""4. 금액산정 — 결정론 계산기.

**여기에 LLM을 쓰지 않는다.** 환각이 곧 지급액 오류가 되는 자리다.
계산이 틀리면 사람이 돈을 덜 받거나 더 받고, 어느 쪽이든 사고다.

산식은 약관이 정한 순서를 그대로 따른다.

    지급액 = min( (실제부담액 − 공제금액) × 보상비율,  잔여 연간한도 )

- **공제금액**은 통원에만 붙고, "정액 또는 의료비의 N% 중 큰 금액"이다.
- **보상비율**은 자기부담률의 나머지다(급여 20% 자기부담 -> 0.8).
- **연간한도**는 보장종목별로 따로 있고, 이미 지급된 금액을 뺀 잔액이 상한이다.
- **감액기간**은 보장개시 초기의 지급사유에 비율을 곱한다. 다른 요소를
  적용한 **뒤에** 곱한다 — 순서를 바꾸면 한도 판정이 달라진다.

파라미터가 없으면 **계산했다고 말하지 않는다**(`computed=False`).
가드레일이 `HUMAN_REVIEW`로 넘긴다. 추측한 값으로 지급액을 내는 것이 최악이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .amount_rules import AmountRule

# 감액기간: 보장개시일부터 이 기간 안의 지급사유는 약관이 정한 비율만 지급한다.
REDUCTION_PERIOD_DAYS = 730
REDUCTION_RATE = 0.5


@dataclass(frozen=True)
class Amount:
    value: int
    computed: bool
    basis: str
    # 어떤 조항의 값을 썼는지. 근거 없이 나온 금액은 쓸 수 없다.
    source_articles: tuple[str, ...] = ()


def compute(
    claimed_amount: int,
    days_since_enrollment: int | None,
    *,
    rule: AmountRule | None = None,
    inpatient: bool = True,
    already_paid_this_year: int = 0,
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

    # 1. 공제 — 통원에만. "정액 또는 의료비의 N% 중 큰 금액".
    deductible = 0
    if not inpatient:
        by_rate = int(claimed_amount * (rule.outpatient_deductible_rate or 0))
        deductible = max(rule.outpatient_deductible or 0, by_rate)
        steps.append(f"통원 공제 {deductible:,}원(정액과 비율 중 큰 쪽)")
    payable = max(0, claimed_amount - deductible)

    # 2. 보상비율.
    rate = rule.reimburse_rate or 0.0
    payable = int(payable * rate)
    steps.append(f"보상비율 {rate:.0%}")

    # 3. 감액기간 — 다른 요소를 적용한 뒤에 곱한다.
    if days_since_enrollment < REDUCTION_PERIOD_DAYS:
        payable = int(payable * REDUCTION_RATE)
        steps.append(
            f"가입 후 {days_since_enrollment}일 — 감액기간"
            f"({REDUCTION_PERIOD_DAYS}일) 안이라 {REDUCTION_RATE:.0%}"
        )

    # 4. 연간한도 — 이미 지급된 금액을 뺀 잔액이 상한이다.
    remaining = max(0, (rule.annual_limit or 0) - already_paid_this_year)
    if payable > remaining:
        steps.append(f"연간한도 잔액 {remaining:,}원으로 제한")
        payable = remaining
    else:
        steps.append(f"연간한도 잔액 {remaining:,}원 이내")

    return Amount(
        payable,
        computed=True,
        basis=" / ".join(steps),
        source_articles=rule.source_articles,
    )
