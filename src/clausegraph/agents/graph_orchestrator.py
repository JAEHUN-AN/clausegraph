"""같은 5단계를 LangGraph로 다시 짰다 — 비교용.

    uv run --extra graph --extra langgraph python -m clausegraph.agents.compare

## 왜 다시 짜나

공고의 우대사항에 `LangChain·LangGraph 등 Agent Framework`가 있고
notes/037의 판정은 **없음**이었다. 그렇다고 자체 오케스트레이터를 버리고
갈아타는 것은 이 프로젝트의 방식이 아니다 — **둘을 같은 청구에 걸어 재는
것**이 방식이다.

## 스텝 함수를 두 벌 만들지 않았다

notes/036에서 PDF 파서를 만들 때 쓴 것과 같은 규칙이다.

> PDF용 조문 규칙을 새로 쓰면 불일치가 나와도 원인을 못 가린다 —
> 레이아웃 탓인지 규칙 탓인지.

여기도 같다. 버전 확정·보장 탐색·면책 검증·금액 산정·가드레일은 **기존
함수를 그대로 부른다.** 이 파일이 새로 쓰는 것은 **스텝을 잇는 방법**뿐이다.
그래야 두 구현의 차이가 곧 오케스트레이션의 차이가 된다.

`_finalize`와 `_days_since`도 `orchestrator`의 것을 그대로 쓴다. 비공개
이름을 가져오는 것이 보기 좋지 않지만, 판정을 조립하는 규칙이 두 벌이
되는 것보다 낫다 — notes/023이 그 대가를 알려 줬다.

## 흐름은 같다

```
    사실정리 -> 버전확정 -> 보장탐색 -> 면책검증 -> 금액산정 -> 검증/심판
                    |           |           |
                    +-----------+-----------+--> 조기 종료
```

자체 구현에서는 `if ... return`이던 것이 여기서는 조건부 엣지다. **표현이
달라질 뿐 순서는 같아야 하고, 같은지를 `compare.py`가 확인한다.**

## 한 군데는 일부러 다르게 짰다

자체 구현은 종료 지점이 넷이고 넷 다 `_finalize`(가드레일)를 부른다.
그건 **약속**이지 구조가 아니다 — 다섯 번째 `return`을 쓰면서 빠뜨리면
그 경로만 가드레일 없이 나간다.

여기서는 종료 노드가 판정 **초안**만 채우고, 엣지 넷이 전부 `검증심판`
노드로 모인다. 가드레일을 건너뛰려면 엣지를 그려야 하고, 엣지는
`build()`에 한눈에 보인다. notes/039에서 "게이트를 한 곳에만 걸면 94.5%가
샌다"를 보고 난 뒤라 이 차이가 값어치 있어 보인다.

**그래도 판정은 같아야 한다.** 다르면 이 구조 변경이 동작을 바꾼 것이므로
비교가 아니라 사고다.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from neo4j import Driver

from ..access import Principal
from ..observability import REGISTRY
from . import amount as amount_agent
from . import guardrails
from .amount_rules import find_rule
from .coverage import find_coverage, resolve_version
from .exclusion import screen
from .models import Adjudication, Claim, Decision, Evidence, StepResult
from .orchestrator import MAX_EVIDENCE, _days_since, _finalize, _timed


def _keep_last(_current, incoming):
    """상태 병합 규칙. 분기가 합류하지 않으므로 나중 값이 이긴다."""
    return incoming


class AdjudicationState(TypedDict, total=False):
    """그래프가 들고 다니는 상태.

    자체 구현에서는 지역 변수였던 것들이다. **이 차이가 LangGraph를 쓸 때의
    실제 비용이다** — 스텝 사이에 넘길 값을 전부 타입으로 선언해야 하고,
    선언을 빠뜨리면 다음 노드에서 조용히 `None`이 된다.
    """

    claim: Annotated[Claim, _keep_last]
    principal: Annotated[Principal, _keep_last]
    masked: Annotated[bool, _keep_last]
    version: Annotated[str | None, _keep_last]
    coverage: Annotated[tuple[Evidence, ...], _keep_last]
    certain: Annotated[list, _keep_last]
    uncertain: Annotated[list, _keep_last]
    steps: Annotated[list[StepResult], _keep_last]
    # 종료 노드가 채우는 **판정 초안**. 가드레일을 거치기 전 값이다.
    draft: Annotated[dict, _keep_last]
    result: Annotated[Adjudication | None, _keep_last]


def _mask(state: AdjudicationState) -> AdjudicationState:
    claim = state["claim"]
    narrative, masked = guardrails.mask_pii(claim.narrative)
    if masked:
        claim = claim.model_copy(update={"narrative": narrative})
    # 권한 검사는 자체 구현과 같은 자리에 둔다. 예외를 그래프 밖으로
    # 그대로 던진다 — 권한 거절은 판정이 아니다.
    state["principal"].require(claim.product)
    return {"claim": claim, "masked": masked, "steps": []}


def _resolve(state: AdjudicationState, driver: Driver) -> AdjudicationState:
    claim = state["claim"]
    version, step = _timed(
        "보장탐색:버전확정",
        lambda: resolve_version(
            driver, claim.enrolled_on, claim.product, principal=state["principal"]
        ),
    )
    summary = (
        f"가입일 {claim.enrolled_on} -> 약관 {version}"
        if version
        else f"가입일 {claim.enrolled_on}에 적용되던 약관을 찾지 못했다"
    )
    return {
        "version": version,
        "steps": [*state["steps"], step(ok=version is not None, summary=summary)],
    }


def _coverage(state: AdjudicationState, driver: Driver) -> AdjudicationState:
    claim = state["claim"]
    evidence, step = _timed(
        "보장탐색:조항",
        lambda: find_coverage(
            driver, claim.product, state["version"], principal=state["principal"]
        ),
    )
    return {
        "coverage": evidence,
        "steps": [
            *state["steps"],
            step(
                ok=bool(evidence),
                summary=f"보장 조항 {len(evidence)}개",
                evidence=evidence[:MAX_EVIDENCE],
            ),
        ],
    }


def _exclusions(state: AdjudicationState, driver: Driver) -> AdjudicationState:
    claim = state["claim"]
    screened, step = _timed(
        "면책검증",
        lambda: screen(driver, claim, state["version"], principal=state["principal"]),
    )
    hits, considered = screened
    certain = [hit for hit in hits if hit.certain]
    uncertain = [hit for hit in hits if not hit.certain]
    return {
        "certain": certain,
        "uncertain": uncertain,
        "steps": [
            *state["steps"],
            step(
                ok=True,
                summary=(
                    f"면책 {considered}건을 전부 검토 -> "
                    f"확실 {len(certain)}, 불확실 {len(uncertain)}"
                ),
                evidence=tuple(hit.evidence for hit in hits[:MAX_EVIDENCE]),
                detail={"considered": considered, "certain": len(certain)},
            ),
        ],
    }


def _amount(state: AdjudicationState) -> AdjudicationState:
    claim = state["claim"]
    uncertain = state["uncertain"]
    coverage_name = next(
        (
            hit.evidence.node_uid.split("#")[1]
            for hit in uncertain
            if "#" in hit.evidence.node_uid
        ),
        None,
    )
    rule = find_rule(claim.product, coverage_name, claim.diagnosis_codes)
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
    steps = [
        *state["steps"],
        step(
            ok=computed.computed,
            summary=computed.basis,
            detail={"rule": f"{claim.product}/{coverage_name or '미지정'}"},
        ),
    ]
    decision = (
        Decision.PARTIAL if computed.value < claim.claimed_amount else Decision.PAID
    )
    evidence = (
        *state["coverage"][:2],
        *(hit.evidence for hit in uncertain[:2]),
        *(e for hit in uncertain[:2] for e in hit.exceptions),
    )
    return {
        "steps": steps,
        "draft": {
            "decision": decision,
            "reason": computed.basis,
            "evidence": evidence,
            "amount_computed": computed.computed,
            "uncertain": bool(uncertain),
            "amount": computed.value,
            "amount_is_upper_bound": computed.is_upper_bound,
            "amount_is_lower_bound": computed.is_lower_bound,
        },
    }


def _no_version(state: AdjudicationState) -> AdjudicationState:
    REGISTRY.increment("needs_docs:버전 없음")
    return {
        "version": None,
        "draft": {
            "decision": Decision.NEEDS_DOCS,
            "reason": "가입 시점의 약관을 특정하지 못했다",
            "evidence": (),
            "amount_computed": False,
            "uncertain": False,
        },
    }


def _no_coverage(state: AdjudicationState) -> AdjudicationState:
    REGISTRY.increment("needs_docs:그 시점에 상품 없음")
    product = state["claim"].product
    return {
        "draft": {
            "decision": Decision.NEEDS_DOCS,
            "reason": f"{product}의 보장 조항을 찾지 못했다",
            "evidence": (),
            "amount_computed": False,
            "uncertain": False,
        },
    }


def _denied(state: AdjudicationState) -> AdjudicationState:
    certain = state["certain"]
    return {
        "draft": {
            "decision": Decision.DENIED,
            "reason": certain[0].reason,
            "evidence": (
                *(hit.evidence for hit in certain),
                *(e for hit in certain for e in hit.exceptions),
            ),
            # 코드로 확정된 면책이므로 불확실 히트가 결론을 흔들지 않는다.
            "amount_computed": True,
            "uncertain": False,
        },
    }


def _verdict(state: AdjudicationState) -> AdjudicationState:
    """검증/심판 — **모든 경로가 여기를 지난다.**

    이것이 이 구현에서 자체 구현보다 나은 유일한 지점이다. 자체
    `orchestrator`는 종료 지점이 넷이고 넷 다 `_finalize`를 부르는데,
    그건 **약속**이지 구조가 아니다. 다섯 번째 `return`을 추가하면서
    `_finalize`를 빠뜨리면 그 경로만 가드레일 없이 나간다.

    여기서는 종료 노드가 초안만 채우고 엣지가 전부 이 노드로 모인다.
    가드레일을 건너뛰려면 **엣지를 그려야** 하고, 엣지는 `build()`에
    한눈에 보인다.

    notes/039에서 "게이트를 한 곳에만 걸면 94.5%가 샌다"를 보고 난 뒤라
    이 성질이 값어치 있어 보인다.
    """
    draft = state["draft"]
    return {
        "result": _finalize(
            state["claim"],
            draft["decision"],
            draft["reason"],
            draft["evidence"],
            state.get("version"),
            state["steps"],
            amount_computed=draft["amount_computed"],
            uncertain=draft["uncertain"],
            masked=state["masked"],
            amount=draft.get("amount", 0),
            amount_is_upper_bound=draft.get("amount_is_upper_bound", False),
            amount_is_lower_bound=draft.get("amount_is_lower_bound", False),
        )
    }


def build(driver: Driver):
    """5단계를 StateGraph로 잇는다.

    자체 구현의 `if ... return`이 여기서는 조건부 엣지다. 그림으로 그리면
    더 잘 보인다는 것이 이 방식의 값어치인데, **이 흐름은 분기가 전부
    종료로 가는 직선**이라 그 값어치가 크지 않다. 분기가 합류하거나
    되돌아가는 흐름이었다면 달랐을 것이다.
    """
    graph = StateGraph(AdjudicationState)

    graph.add_node("사실정리", _mask)
    graph.add_node("버전확정", lambda state: _resolve(state, driver))
    graph.add_node("보장탐색", lambda state: _coverage(state, driver))
    graph.add_node("면책검증", lambda state: _exclusions(state, driver))
    graph.add_node("금액산정", _amount)
    graph.add_node("버전없음", _no_version)
    graph.add_node("보장없음", _no_coverage)
    graph.add_node("부지급", _denied)
    graph.add_node("검증심판", _verdict)

    graph.add_edge(START, "사실정리")
    graph.add_edge("사실정리", "버전확정")
    graph.add_conditional_edges(
        "버전확정",
        lambda state: "보장탐색" if state["version"] else "버전없음",
        {"보장탐색": "보장탐색", "버전없음": "버전없음"},
    )
    graph.add_conditional_edges(
        "보장탐색",
        lambda state: "면책검증" if state["coverage"] else "보장없음",
        {"면책검증": "면책검증", "보장없음": "보장없음"},
    )
    graph.add_conditional_edges(
        "면책검증",
        lambda state: "부지급" if state["certain"] else "금액산정",
        {"부지급": "부지급", "금액산정": "금액산정"},
    )
    # **네 갈래가 전부 검증/심판으로 모인다.** 가드레일을 건너뛰는 경로를
    # 만들려면 엣지를 그려야 하고, 그리면 여기서 보인다.
    for terminal in ("금액산정", "버전없음", "보장없음", "부지급"):
        graph.add_edge(terminal, "검증심판")
    graph.add_edge("검증심판", END)
    return graph.compile()


def adjudicate(driver: Driver, claim: Claim, *, principal: Principal) -> Adjudication:
    """자체 `orchestrator.adjudicate`와 같은 시그니처·같은 반환."""
    compiled = _compiled(driver)
    state = compiled.invoke({"claim": claim, "principal": principal})
    return state["result"]


_CACHE: dict[int, object] = {}


def _compiled(driver: Driver):
    """컴파일은 한 번만. 청구마다 다시 하면 그 비용이 비교에 섞인다."""
    key = id(driver)
    if key not in _CACHE:
        _CACHE[key] = build(driver)
    return _CACHE[key]
