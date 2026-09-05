"""4. 금액산정 — 결정론 계산기.

**여기에 LLM을 쓰지 않는다.** 환각이 곧 지급액 오류가 되는 자리다.
계산이 틀리면 사람이 돈을 덜 받거나 더 받고, 어느 쪽이든 사고다.

지금 구현은 뼈대다. 실제 지급액은 자기부담금·보상한도·감액기간·비례보상
같은 규칙이 상품마다 달라 약관에서 뽑아야 한다. 그 값을 채우기 전까지는
**계산했다고 말하지 않는다** — `computed=False`로 표시해 사람에게 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 감액기간: 보장개시일부터 이 기간 안의 지급사유는 약관이 정한 비율만 지급한다.
REDUCTION_PERIOD_DAYS = 730
REDUCTION_RATE = 0.5


@dataclass(frozen=True)
class Amount:
    value: int
    computed: bool
    basis: str


def compute(claimed_amount: int, days_since_enrollment: int | None) -> Amount:
    """청구액과 가입 경과일로 지급액을 계산한다."""
    if claimed_amount <= 0:
        return Amount(0, computed=False, basis="청구액이 없어 계산할 수 없다")

    if days_since_enrollment is None:
        return Amount(
            0, computed=False, basis="사고일이 없어 감액기간을 판단할 수 없다"
        )

    if days_since_enrollment < REDUCTION_PERIOD_DAYS:
        value = int(claimed_amount * REDUCTION_RATE)
        return Amount(
            value,
            computed=True,
            basis=(
                f"가입 후 {days_since_enrollment}일 — 감액기간"
                f"({REDUCTION_PERIOD_DAYS}일) 안이라 {REDUCTION_RATE:.0%} 지급"
            ),
        )

    return Amount(
        claimed_amount,
        computed=True,
        basis=f"가입 후 {days_since_enrollment}일 — 감액기간을 지나 전액",
    )
