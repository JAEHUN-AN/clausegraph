"""자체 오케스트레이터 vs LangGraph — 같은 청구에 둘을 걸어 잰다.

    uv run --extra graph --extra langgraph python -m clausegraph.agents.compare --claims 200

## 무엇을 재는가

| 축 | 왜 |
|---|---|
| 판정 일치율 | 다르면 둘 중 하나가 틀린 것이다. 라벨이 필요 없다 |
| p50 / p95 지연 | 프레임워크가 스텝마다 붙이는 비용 |
| 코드량 | 같은 흐름을 적는 데 드는 줄 수 |
| 의존성 | 폐쇄망에 들일 수 있는가 |

**판정 일치율에 라벨이 필요 없는 것이 이 비교의 핵심이다.** 스텝 함수를
두 벌 만들지 않았으므로(`graph_orchestrator` 참조), 두 구현이 다른 답을
내면 그것은 곧 오케스트레이션의 차이다. notes/036에서 같은 약관을 PDF로
한 번 더 읽어 파서를 채점한 것과 같은 수법이다.

청구는 `bench.py`의 합성기를 그대로 쓴다. 실제 청구 데이터나 개인정보를
쓰지 않는다.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

from ..access import Principal
from ..observability import REGISTRY
from . import graph_orchestrator, orchestrator
from .bench import SEED, WARMUP_CLAIMS, synthesize
from .extract import extract_claim
from .models import Adjudication
from .terminology import lookup

COMPARE_PRINCIPAL = Principal.everything("compare")

# 코드량을 셀 파일. 오케스트레이션만 센다 — 스텝 함수는 공유하므로
# 양쪽에 똑같이 들어간다.
_SELF_SOURCE = Path("src/clausegraph/agents/orchestrator.py")
_GRAPH_SOURCE = Path("src/clausegraph/agents/graph_orchestrator.py")


def _mismatch(left: Adjudication, right: Adjudication) -> str | None:
    """두 판정이 다른 곳. 같으면 None.

    **증거와 가드레일까지 본다.** 판정 문자열만 맞춰 보면 "둘 다 DENIED인데
    근거 조항이 다른" 경우를 놓친다. 이 시스템에서 근거는 산출물이다.
    """
    if left.decision is not right.decision:
        return f"판정 {left.decision} != {right.decision}"
    if left.amount != right.amount:
        return f"금액 {left.amount} != {right.amount}"
    if left.applied_version != right.applied_version:
        return f"판본 {left.applied_version} != {right.applied_version}"
    if left.guardrails != right.guardrails:
        return f"가드레일 {left.guardrails} != {right.guardrails}"
    left_uids = tuple(item.node_uid for item in left.evidence)
    right_uids = tuple(item.node_uid for item in right.evidence)
    if left_uids != right_uids:
        return f"근거 {len(left_uids)}건 != {len(right_uids)}건"
    if left.reason != right.reason:
        return "사유 문구"
    return None


def _source_lines(path: Path) -> int:
    """주석과 빈 줄을 뺀 줄 수. 문서화 습관 차이를 세지 않으려는 것이다."""
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    count, in_doc = 0, False
    for raw in lines:
        line = raw.strip()
        if line.startswith(('"""', "'''")) and line.count('"""') == 1:
            in_doc = not in_doc
            continue
        if in_doc or not line or line.startswith("#"):
            continue
        count += 1
    return count


def measure_agreement(count: int = 60) -> dict[str, float]:
    """회귀 게이트용. 판정 일치율만 돌려준다(notes/041).

    지연은 기준선에 넣지 않는다 — 같은 기계에서 재도 0.3ms 대에서는
    다른 프로세스 하나에 두 배가 흔들린다. **재현되지 않는 수를 게이트에
    걸면 게이트를 끄게 된다.**
    """
    measured, _, _, mismatches = _run(count)
    return {"agreement": (measured - len(mismatches)) / max(measured, 1)}


def run(count: int) -> int:
    measured, self_ms, graph_ms, mismatches = _run(count)
    _report(measured, self_ms, graph_ms, mismatches)
    return 0


def _run(count: int):
    rng = random.Random(SEED)
    claims = synthesize(count, rng)
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )

    self_ms: list[float] = []
    graph_ms: list[float] = []
    mismatches: list[tuple[str, str]] = []
    measured = 0

    try:
        for index, (product, enrolled_on, narrative, history) in enumerate(claims):
            claim = extract_claim(
                f"CMP-{index:04d}", product, enrolled_on, narrative,
                enrich=lookup, history=history,
            )

            # 스텝 지연은 REGISTRY가 공유 상태라 섞인다. 여기서는 벽시계로
            # 전체만 재고, 매 건 초기화해 서로의 표본이 섞이지 않게 한다.
            REGISTRY.reset()
            started = time.perf_counter()
            mine = orchestrator.adjudicate(driver, claim, principal=COMPARE_PRINCIPAL)
            mine_ms = (time.perf_counter() - started) * 1000

            REGISTRY.reset()
            started = time.perf_counter()
            theirs = graph_orchestrator.adjudicate(
                driver, claim, principal=COMPARE_PRINCIPAL
            )
            theirs_ms = (time.perf_counter() - started) * 1000

            # 웜업은 캐시가 비어 있어 양쪽 다 느리다. 버리지 않고 빼 둔다.
            if index < WARMUP_CLAIMS:
                continue
            measured += 1
            self_ms.append(mine_ms)
            graph_ms.append(theirs_ms)
            difference = _mismatch(mine, theirs)
            if difference is not None:
                mismatches.append((claim.claim_id, difference))
    finally:
        driver.close()

    return measured, self_ms, graph_ms, mismatches


def _report(measured, self_ms, graph_ms, mismatches) -> None:
    agree = measured - len(mismatches)
    print(f"\n=== 자체 vs LangGraph (청구 {measured}건, 웜업 {WARMUP_CLAIMS} 제외) ===\n")
    print(f"{'축':16s} {'자체':>14s} {'LangGraph':>14s}")
    print(
        f"{'p50 지연':16s} {statistics.median(self_ms):12.2f}ms "
        f"{statistics.median(graph_ms):12.2f}ms"
    )
    print(
        f"{'p95 지연':16s} {_p95(self_ms):12.2f}ms {_p95(graph_ms):12.2f}ms"
    )
    print(
        f"{'오케스트레이션 줄':16s} {_source_lines(_SELF_SOURCE):14d} "
        f"{_source_lines(_GRAPH_SOURCE):14d}"
    )
    overhead = statistics.median(graph_ms) - statistics.median(self_ms)
    ratio = statistics.median(graph_ms) / max(statistics.median(self_ms), 1e-9)
    print(f"\n판정 일치 {agree}/{measured} = {agree / max(measured, 1):.1%}")
    print(f"프레임워크 부담 p50 +{overhead:.2f}ms ({ratio:.2f}배)")
    if mismatches:
        print(f"\n불일치 {len(mismatches)}건:")
        for claim_id, difference in mismatches[:20]:
            print(f"  {claim_id}  {difference}")
    else:
        print("\n불일치 없음 — 두 구현이 같은 답을 낸다.")


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered)) - 1))
    return ordered[index]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="오케스트레이터 두 구현 비교")
    parser.add_argument("--claims", type=int, default=200)
    args = parser.parse_args()
    return run(args.claims)


if __name__ == "__main__":
    raise SystemExit(main())
