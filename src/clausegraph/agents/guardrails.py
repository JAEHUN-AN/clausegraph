"""가드레일 — 판정이 넘지 말아야 할 선.

공고의 "핵심 업무 시스템 연동 및 안전장치(가드레일) 설계"에 대응한다.
넷 다 **판정을 더 보수적인 쪽으로만** 움직인다. 가드레일이 지급을 만들어
내는 일은 없다.
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
