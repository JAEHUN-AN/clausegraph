"""LangGraph 구현의 **구조**를 고정한다.

판정이 같은지는 `agents/compare.py`가 그래프와 색인을 띄워 확인한다
(CI에서 못 돈다). 여기서 지키는 것은 DB 없이 확인할 수 있는 성질 —
**모든 경로가 가드레일을 지나는가** 다.

이 성질이 이 구현을 만든 이유였다(notes/040). 그러니 테스트로 못 박는다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph", reason="langgraph extra가 없으면 건너뛴다")

from clausegraph.agents.graph_orchestrator import build  # noqa: E402

VERDICT = "검증심판"
TERMINALS = ("금액산정", "버전없음", "보장없음", "부지급")


def _graph():
    # 노드 함수는 부르지 않고 구조만 본다. 드라이버가 필요 없다.
    return build(None).get_graph()


def test_every_terminal_routes_through_the_verdict_node() -> None:
    """네 갈래가 전부 검증/심판으로 모여야 한다.

    자체 `orchestrator`는 종료 지점 넷이 각자 `_finalize`를 부른다 —
    그건 약속이지 구조가 아니다. 여기서는 엣지가 강제한다.
    """
    targets = {
        edge.source: edge.target for edge in _graph().edges if edge.source in TERMINALS
    }

    assert set(targets) == set(TERMINALS), "종료 노드가 늘었거나 줄었다"
    assert set(targets.values()) == {VERDICT}


def test_no_terminal_reaches_end_without_the_verdict_node() -> None:
    """가드레일을 건너뛰고 끝나는 경로가 없어야 한다.

    새 종료 노드를 더하면서 `검증심판`으로 잇는 것을 잊으면 여기서 걸린다.
    """
    to_end = {edge.source for edge in _graph().edges if edge.target == "__end__"}

    assert to_end == {VERDICT}


def test_the_verdict_node_is_the_only_finalizer() -> None:
    """`_finalize`를 부르는 곳이 한 군데여야 한다.

    초안을 만드는 노드가 스스로 판정을 확정해 버리면 위의 엣지 검사가
    통과하면서도 가드레일이 두 번 돌거나 건너뛰게 된다.
    """
    from pathlib import Path

    source = Path("src/clausegraph/agents/graph_orchestrator.py").read_text(
        encoding="utf-8"
    )

    # import 한 줄과 `_verdict` 안의 호출 한 번.
    assert source.count("_finalize(") == 1


def test_flow_order_matches_the_self_implementation() -> None:
    """스텝 순서가 자체 구현과 같아야 한다.

    이 순서 자체가 안전장치다(notes/009) — 버전을 못 정하면 보장을 찾을 수
    없고, 보장이 없으면 면책을 따질 일이 없다.
    """
    edges = {(edge.source, edge.target) for edge in _graph().edges}

    assert ("__start__", "사실정리") in edges
    assert ("사실정리", "버전확정") in edges
    assert ("버전확정", "보장탐색") in edges
    assert ("보장탐색", "면책검증") in edges
    assert ("면책검증", "금액산정") in edges


def test_each_failure_branch_has_its_own_exit() -> None:
    """앞 단계가 실패하면 뒤로 가지 않는다."""
    edges = {(edge.source, edge.target) for edge in _graph().edges}

    assert ("버전확정", "버전없음") in edges
    assert ("보장탐색", "보장없음") in edges
    assert ("면책검증", "부지급") in edges
    # 확정 부지급은 금액산정을 거치지 않는다.
    assert ("부지급", "금액산정") not in edges
