"""가드레일 — 판정이 넘지 말아야 할 선.

공고의 "핵심 업무 시스템 연동 및 안전장치(가드레일) 설계"에 대응한다.
여섯 다 **판정을 더 보수적인 쪽으로만** 움직인다. 가드레일이 지급을 만들어
내는 일은 없다.

다섯은 **보험사가 더 주는 위험**을 막는다. 마지막 하나는 방향이 반대다 —
`amount_lower_bound`는 **청구인이 덜 받는 위험**을 막는다. 부지급도 사고지만
과소지급도 사고이고, 분쟁조정사례의 상당수가 그쪽이다(notes/028).
"""

from __future__ import annotations

import re

from .models import Adjudication, Decision, Evidence

# 주민등록번호·연락처는 판정에 필요 없다. 남기지 않는다.
_RRN_RE = re.compile(r"\b(\d{6})[-\s]?([1-4]\d{6})\b")
_PHONE_RE = re.compile(r"\b(01[016-9])[-\s]?(\d{3,4})[-\s]?(\d{4})\b")

GROUNDING = "grounding_gate"
AMOUNT_NOT_COMPUTED = "amount_not_computed"
UNCERTAIN_EXCLUSION = "uncertain_exclusion"
PII_MASKED = "pii_masked"
AMOUNT_UPPER_BOUND = "amount_upper_bound"
AMOUNT_LOWER_BOUND = "amount_lower_bound"


def mask_pii(text: str) -> tuple[str, bool]:
    """주민번호·전화번호를 지운다."""
    masked, rrn = _RRN_RE.subn(r"\1-*******", text)
    masked, phone = _PHONE_RE.subn(r"\1-****-\3", masked)
    return masked, bool(rrn or phone)


def requires_grounding(decision: Decision) -> bool:
    """근거 조항 없이는 낼 수 없는 판정인가."""
    return decision in (Decision.PAID, Decision.DENIED, Decision.PARTIAL)


def apply(
    adjudication: Adjudication,
    *,
    amount_computed: bool,
    has_uncertain_exclusion: bool,
    amount_is_upper_bound: bool = False,
    amount_is_lower_bound: bool = False,
) -> Adjudication:
    """판정에 가드레일을 건다. 통과하지 못하면 강등한다."""
    triggered = list(adjudication.guardrails)
    decision = adjudication.decision
    reason = adjudication.reason
    amount = adjudication.amount

    # 1. Grounding gate — 조항을 가리키지 못하면 결론을 낼 수 없다.
    if requires_grounding(decision) and not _has_clause(adjudication.evidence):
        triggered.append(GROUNDING)
        decision = Decision.NEEDS_DOCS
        reason = "근거 조항을 특정하지 못했다 — 추가 확인이 필요하다"
        amount = 0

    # 2. 금액을 코드로 계산하지 못했으면 지급액을 말하지 않는다.
    if decision in (Decision.PAID, Decision.PARTIAL) and not amount_computed:
        triggered.append(AMOUNT_NOT_COMPUTED)
        decision = Decision.HUMAN_REVIEW
        reason = "지급액을 계산할 근거가 부족하다 — 심사자 확인이 필요하다"
        amount = 0

    # 3. 확인 못 한 면책이 남아 있는데 지급하지 않는다.
    #    위험한 방향은 이쪽이다 — 걸릴지도 모르는 면책을 두고 돈을 내주는 것.
    #    반대로 코드로 확정된 면책에 따른 부지급은 그대로 둔다.
    if decision in (Decision.PAID, Decision.PARTIAL) and has_uncertain_exclusion:
        triggered.append(UNCERTAIN_EXCLUSION)
        decision = Decision.HUMAN_REVIEW
        reason = "확인하지 못한 면책 가능성이 남아 있다 — 심사자 확인이 필요하다"
        amount = 0

    # 4. 공제를 끝까지 계산하지 못했으면 그 금액은 지급액이 아니라 상한이다.
    #    상한을 지급액으로 내주면 과다지급이 된다. 계산이 "됐다"고 해도
    #    항이 빠졌다면 여기서 막는다.
    if decision in (Decision.PAID, Decision.PARTIAL) and amount_is_upper_bound:
        triggered.append(AMOUNT_UPPER_BOUND)
        decision = Decision.HUMAN_REVIEW
        reason = "공제를 끝까지 계산하지 못했다 — 계산된 금액은 상한이다"
        amount = 0

    # 5. 계산이 실제보다 **작을** 수 있으면 그것도 사람에게 넘긴다.
    #    다른 가드레일과 방향이 반대다 — 이건 청구인을 위한 것이다.
    #    이 금액을 그대로 지급하면 받아야 할 것을 덜 주게 된다.
    if decision in (Decision.PAID, Decision.PARTIAL) and amount_is_lower_bound:
        triggered.append(AMOUNT_LOWER_BOUND)
        decision = Decision.HUMAN_REVIEW
        reason = (
            "계산된 금액이 실제보다 작을 수 있다 — 청구인이 더 받을 수 있으므로 "
            "심사자 확인이 필요하다"
        )
        amount = 0

    return adjudication.model_copy(
        update={
            "decision": decision,
            "reason": reason,
            "amount": amount,
            "guardrails": tuple(triggered),
        }
    )


def _has_clause(evidence: tuple[Evidence, ...]) -> bool:
    return any(item.node_uid for item in evidence)
