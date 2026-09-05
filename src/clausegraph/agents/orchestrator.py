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
from datetime import date

from neo4j import Driver

from ..observability import REGISTRY
from . import amount as amount_agent
from . import guardrails
from .amount_rules import find_rule
from .coverage import find_coverage, resolve_version
from .exclusion import screen
from .models import Adjudication, Claim, Decision, Evidence, StepResult

MAX_EVIDENCE = 6


def adjudicate(driver: Driver, claim: Claim) -> Adjudication:
    steps: list[StepResult] = []

    masked_narrative, masked = guardrails.mask_pii(claim.narrative)
    if masked:
        claim = claim.model_copy(update={"narrative": masked_narrative})

    version, step = _timed(
        "보장탐색:버전확정", lambda: resolve_version(driver, claim.enrolled_on)
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
        return _finalize(
            claim, Decision.NEEDS_DOCS, "가입 시점의 약관을 특정하지 못했다",
            (), None, steps, amount_computed=False, uncertain=False, masked=masked,
        )

    coverage_evidence, step = _timed(
        "보장탐색:조항", lambda: find_coverage(driver, claim.product, version)
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
        return _finalize(
            claim, Decision.NEEDS_DOCS, f"{claim.product}의 보장 조항을 찾지 못했다",
            (), version, steps, amount_computed=False, uncertain=False, masked=masked,
        )

    screened, step = _timed("면책검증", lambda: screen(driver, claim, version))
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
        return _finalize(
            claim, Decision.DENIED, certain[0].reason,
            tuple(hit.evidence for hit in certain), version, steps,
            # 코드로 확정된 면책이므로 불확실 히트가 결론을 흔들지 않는다.
            amount_computed=True, uncertain=False, masked=masked,
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
    evidence = (*coverage_evidence[:2], *(hit.evidence for hit in uncertain[:2]))
    return _finalize(
        claim, decision, computed.basis, evidence, version, steps,
        amount_computed=computed.computed, uncertain=bool(uncertain),
        masked=masked, amount=computed.value,
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
        draft, amount_computed=amount_computed, has_uncertain_exclusion=uncertain
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
