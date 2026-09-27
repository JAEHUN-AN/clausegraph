"""5역할 오케스트레이션.

    사실추출 -> 보장탐색 -> 면책검증 -> 금액산정 -> 검증/심판

앞 단계가 실패하면 뒤로 넘어가지 않는다. 적용 약관 버전을 못 정하면
보장을 찾을 수 없고, 보장 조항이 없으면 면책을 따질 일이 없다. 이 순서를
지키는 것 자체가 안전장치다.

스텝마다 걸린 시간과 결과를 남긴다 — 공고의 "실행 흐름·비용·지연시간 추적".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from neo4j import Driver

from ..access import AccessDeniedError, Principal
from ..observability import REGISTRY
from . import amount as amount_agent
from . import guardrails
from .amount_rules import find_rule
from .coverage import find_coverage, resolve_version
from .exclusion import screen
from .models import Adjudication, Claim, Decision, Evidence, StepResult

MAX_EVIDENCE = 6


@dataclass(frozen=True)
class _Draft:
    """가드레일을 거치기 **전**의 판정.

    스텝들이 만드는 것은 이것이고, 이것이 판정이 되는 곳은 `adjudicate`의
    마지막 한 줄뿐이다(notes/040).
    """

    decision: Decision
    reason: str
    evidence: tuple[Evidence, ...]
    version: str | None
    amount_computed: bool
    uncertain: bool
    amount: int = 0
    amount_is_upper_bound: bool = False
    amount_is_lower_bound: bool = False


def adjudicate(driver: Driver, claim: Claim, *, principal: Principal) -> Adjudication:
    """청구 한 건을 판정한다.

    `principal`은 키워드 필수다. 이 값이 아래 세 스텝으로 그대로 흘러가고,
    그중 어느 하나라도 빠뜨리면 그 스텝만 전 상품을 보게 된다 — 판정
    전체가 아니라 **한 스텝만** 새는 것이라 결과만 봐서는 안 보인다
    (notes/039).

    **종료 지점이 하나다.** 예전에는 `_finalize`를 네 군데에서 불렀는데,
    그건 "모든 경로가 가드레일을 지난다"를 **약속**으로 지키는 것이었다.
    다섯 번째 `return`을 쓰면서 빠뜨리면 그 경로만 가드레일 없이 나가고,
    가드레일은 대부분의 청구에서 발동하지 않으므로 결과를 봐서는 안 보인다.

    LangGraph로 같은 흐름을 짜면서 종료 엣지를 한 노드로 모아 봤고
    (notes/040), 그 성질이 프레임워크 없이도 되는 것이라 가져왔다.
    `_run`이 초안을 만들고, 가드레일은 **여기 한 줄**에서만 걸린다.
    """
    steps: list[StepResult] = []
    masked_narrative, masked = guardrails.mask_pii(claim.narrative)
    if masked:
        claim = claim.model_copy(update={"narrative": masked_narrative})

    # 권한 거절은 판정이 아니다. 조회할 수 없는 상품에 대해 NEEDS_DOCS를
    # 내면 "서류를 더 내면 된다"는 뜻이 되는데 사실이 아니다.
    try:
        principal.require(claim.product)
    except AccessDeniedError:
        REGISTRY.increment("access_denied:상품 권한 없음")
        raise

    draft = _run(driver, claim, steps, principal)
    return _finalize(
        claim, draft.decision, draft.reason, draft.evidence, draft.version, steps,
        amount_computed=draft.amount_computed, uncertain=draft.uncertain,
        masked=masked, amount=draft.amount,
        amount_is_upper_bound=draft.amount_is_upper_bound,
        amount_is_lower_bound=draft.amount_is_lower_bound,
    )


def _run(
    driver: Driver, claim: Claim, steps: list[StepResult], principal: Principal
) -> _Draft:
    """스텝을 순서대로 돌려 판정 초안을 만든다. 가드레일은 걸지 않는다."""
    version, step = _timed(
        "보장탐색:버전확정",
        lambda: resolve_version(
            driver, claim.enrolled_on, claim.product, principal=principal
        ),
    )
    steps.append(
        step(
            ok=version is not None,
            summary=(
                f"가입일 {claim.enrolled_on} -> 약관 {version}"
                if version
                else f"가입일 {claim.enrolled_on}에 적용되던 약관을 찾지 못했다"
            ),
        )
    )
    if version is None:
        REGISTRY.increment("needs_docs:버전 없음")
        return _Draft(
            decision=Decision.NEEDS_DOCS,
            reason="가입 시점의 약관을 특정하지 못했다",
            evidence=(), version=None, amount_computed=False, uncertain=False,
        )

    coverage_evidence, step = _timed(
        "보장탐색:조항",
        lambda: find_coverage(driver, claim.product, version, principal=principal),
    )
    steps.append(
        step(
            ok=bool(coverage_evidence),
            summary=f"보장 조항 {len(coverage_evidence)}개",
            evidence=coverage_evidence[:MAX_EVIDENCE],
        )
    )
    if not coverage_evidence:
        # 가입 시점에 그 상품이 없던 경우가 대부분이다 — 실손 특별약관1/2는
        # 2026-05-06에 생겼다. 거절이 맞는 동작이고, 왜 거절했는지 센다.
        REGISTRY.increment("needs_docs:그 시점에 상품 없음")
        return _Draft(
            decision=Decision.NEEDS_DOCS,
            reason=f"{claim.product}의 보장 조항을 찾지 못했다",
            evidence=(), version=version, amount_computed=False, uncertain=False,
        )

    screened, step = _timed(
        "면책검증", lambda: screen(driver, claim, version, principal=principal)
    )
    hits, considered = screened
    certain = [hit for hit in hits if hit.certain]
    uncertain = [hit for hit in hits if not hit.certain]
    steps.append(
        step(
            ok=True,
            summary=(
                f"면책 {considered}건을 전부 검토 -> "
                f"확실 {len(certain)}, 불확실 {len(uncertain)}"
            ),
            evidence=tuple(hit.evidence for hit in hits[:MAX_EVIDENCE]),
            detail={"considered": considered, "certain": len(certain)},
        )
    )

    if certain:
        # 면책 근거와 함께 그 면책의 '다만' 단서가 가리키는 조문도 낸다.
        # "면책에 걸렸다"까지만 말하면 청구인에게는 절반만 답한 것이다 —
        # 예외 조항이 다시 보상을 열어 줄 수 있다(notes/022).
        return _Draft(
            decision=Decision.DENIED,
            reason=certain[0].reason,
            evidence=(
                *(hit.evidence for hit in certain),
                *(e for hit in certain for e in hit.exceptions),
            ),
            version=version,
            # 코드로 확정된 면책이므로 불확실 히트가 결론을 흔들지 않는다.
            amount_computed=True, uncertain=False,
        )

    # 불확실 면책이 가리키는 보장종목이 있으면 그 종목의 파라미터를 쓴다.
    coverage = next(
        (hit.evidence.node_uid.split("#")[1] for hit in uncertain if "#" in hit.evidence.node_uid),
        None,
    )
    rule = find_rule(claim.product, coverage, claim.diagnosis_codes)
    computed, step = _timed(
        "금액산정",
        lambda: amount_agent.compute(
            claim.claimed_amount,
            _days_since(claim),
            rule=rule,
            inpatient=claim.hospital_days > 0,
            institution=claim.institution,
            copay_rate=claim.copay_rate,
            history=claim.history,
            room_charge=claim.room_charge,
            hospital_days=claim.hospital_days,
        ),
    )
    steps.append(
        step(
            ok=computed.computed,
            summary=computed.basis,
            detail={"rule": f"{claim.product}/{coverage or '미지정'}"},
        )
    )

    decision = (
        Decision.PARTIAL if computed.value < claim.claimed_amount else Decision.PAID
    )
    # 사람에게 넘길 때가 예외 조항 정보가 가장 필요한 자리다. 심사자는
    # "이 면책이 걸릴 수도 있다"와 "그런데 예외가 있다"를 함께 봐야 한다.
    evidence = (
        *coverage_evidence[:2],
        *(hit.evidence for hit in uncertain[:2]),
        *(e for hit in uncertain[:2] for e in hit.exceptions),
    )
    return _Draft(
        decision=decision, reason=computed.basis, evidence=evidence, version=version,
        amount_computed=computed.computed, uncertain=bool(uncertain),
        amount=computed.value,
        amount_is_upper_bound=computed.is_upper_bound,
        amount_is_lower_bound=computed.is_lower_bound,
    )


def _finalize(
    claim: Claim,
    decision: Decision,
    reason: str,
    evidence: tuple[Evidence, ...],
    version: str | None,
    steps: list[StepResult],
    *,
    amount_computed: bool,
    uncertain: bool,
    masked: bool,
    amount: int = 0,
    amount_is_upper_bound: bool = False,
    amount_is_lower_bound: bool = False,
) -> Adjudication:
    started = time.perf_counter()
    draft = Adjudication(
        claim_id=claim.claim_id,
        decision=decision,
        amount=amount,
        reason=reason,
        evidence=evidence,
        applied_version=version,
        steps=tuple(steps),
        guardrails=(guardrails.PII_MASKED,) if masked else (),
    )
    final = guardrails.apply(
        draft,
        amount_computed=amount_computed,
        has_uncertain_exclusion=uncertain,
        amount_is_upper_bound=amount_is_upper_bound,
        amount_is_lower_bound=amount_is_lower_bound,
    )
    verdict = StepResult(
        step="검증/심판",
        ok=final.decision is decision,
        summary=(
            f"{decision} 유지"
            if final.decision is decision
            else f"{decision} -> {final.decision} (가드레일 {', '.join(final.guardrails)})"
        ),
        elapsed_ms=(time.perf_counter() - started) * 1000,
        evidence=final.evidence[:MAX_EVIDENCE],
    )
    REGISTRY.increment(f"decision:{final.decision}")
    for name in final.guardrails:
        REGISTRY.increment(f"guardrail:{name}")
    return final.model_copy(update={"steps": (*final.steps, verdict)})


def _days_since(claim: Claim) -> int | None:
    reference = claim.incident_on or date.today()
    return (reference - claim.enrolled_on).days


def _timed(name: str, run: Callable):
    """스텝 실행 시간을 재고, 결과를 StepResult로 감쌀 클로저를 함께 준다."""
    started = time.perf_counter()
    value = run()
    elapsed = (time.perf_counter() - started) * 1000
    REGISTRY.record(name, elapsed)

    def build(*, ok: bool, summary: str, evidence: tuple = (), detail: dict | None = None):
        return StepResult(
            step=name,
            ok=ok,
            summary=summary,
            elapsed_ms=elapsed,
            evidence=evidence,
            detail=detail or {},
        )

    return value, build
