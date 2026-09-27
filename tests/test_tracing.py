"""요청 단위 추적과 회귀 게이트 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from clausegraph.regress import Direction, Metric, Status, _compare
from clausegraph.tracing import TraceStore, current, span, trace


def test_spans_nest_by_depth() -> None:
    """스텝 안의 스텝이 깊이로 남아야 한다.

    평평한 리스트면 "면책검증이 70ms"까지만 말하고 그 안에서 무엇이
    느린지는 못 가른다(notes/011에서 그걸 찾느라 코드를 읽어야 했다).
    """
    with trace("청구") as record, span("면책검증"), span("열거"):
        pass

    assert [(item.name, item.depth) for item in record.spans] == [
        ("면책검증", 0),
        ("열거", 1),
    ]


def test_total_counts_only_top_level_spans() -> None:
    """중첩 스팬을 두 번 더하면 총합이 부풀어 오른다."""
    with trace("청구") as record, span("바깥"), span("안쪽"):
        pass

    assert record.total_ms == pytest.approx(record.spans[0].elapsed_ms)


def test_span_outside_a_trace_does_nothing_and_does_not_raise() -> None:
    """스텝 함수는 평가·적재에서도 불린다.

    거기서 트레이스를 안 열었다고 예외가 나면, 추적을 넣은 것이 시스템을
    더 약하게 만든 셈이 된다.
    """
    assert current() is None

    with span("트레이스 밖") as entry:
        entry.attributes["x"] = 1  # 터지지 않아야 한다

    assert current() is None


def test_failure_is_recorded_and_reraised() -> None:
    with pytest.raises(ValueError), trace("청구") as record, span("면책검증"):
        raise ValueError("터짐")

    assert record.spans[0].ok is False


def test_trace_survives_and_is_findable_by_id() -> None:
    from clausegraph.tracing import TRACES

    with trace("청구") as record:
        pass

    assert TRACES.get(record.trace_id) is record


def test_store_drops_the_oldest_past_the_limit() -> None:
    """오래 도는 프로세스가 메모리를 먹지 않아야 한다."""
    store = TraceStore(limit=2)
    from clausegraph.tracing import Trace

    for index in range(4):
        store.put(Trace(trace_id=f"t{index}"))

    assert len(store) == 2
    assert store.get("t0") is None
    assert store.get("t3") is not None


def test_render_shows_what_decided_not_just_how_long() -> None:
    """트레이스가 집계와 같은 것만 보여 주면 있을 이유가 없다."""
    with trace("CLM-1") as record:
        with span("면책검증") as entry:
            entry.attributes["certain"] = 3
        record.verdict = "HUMAN_REVIEW (가드레일 amount_upper_bound)"

    rendered = record.render()

    assert "certain=3" in rendered
    assert "amount_upper_bound" in rendered


# --- 회귀 게이트 -----------------------------------------------------------


def test_recall_may_rise_but_not_fall() -> None:
    metric = Metric("recall", Direction.HIGHER_IS_BETTER, tolerance=0.02)

    assert _compare(metric, 0.50, 0.60)[0] is Status.PASS
    assert _compare(metric, 0.50, 0.49)[0] is Status.PASS  # 허용치 안
    assert _compare(metric, 0.50, 0.40)[0] is Status.FAIL


def test_leaks_may_fall_but_not_rise() -> None:
    metric = Metric("leaks", Direction.LOWER_IS_BETTER)

    assert _compare(metric, 0.0, 0.0)[0] is Status.PASS
    assert _compare(metric, 10.0, 0.0)[0] is Status.PASS
    assert _compare(metric, 0.0, 1.0)[0] is Status.FAIL


def test_exact_metrics_fail_in_either_direction() -> None:
    """구조 지표는 늘어도 줄어도 설명이 필요하다."""
    metric = Metric("finalize_call_sites", Direction.EXACT)

    assert _compare(metric, 2.0, 2.0)[0] is Status.PASS
    assert _compare(metric, 2.0, 3.0)[0] is Status.FAIL
    assert _compare(metric, 2.0, 1.0)[0] is Status.FAIL


def test_the_baseline_file_is_committed() -> None:
    """기준선이 리포에 없으면 게이트가 매번 '기준선 없음'이 된다."""
    assert Path("eval_baselines.json").exists()


def test_structure_metrics_need_no_database() -> None:
    """CI에서 실제로 도는 부분이라 전제 없이 돌아야 한다."""
    from clausegraph.regress import _measure_structure

    measured = _measure_structure()

    assert measured["finalize_call_sites"] == 2.0
    assert measured["mcp_tools"] == measured["guarded_tools"]
