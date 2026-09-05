"""지연 집계 테스트."""

from __future__ import annotations

import pytest

from clausegraph.observability import Registry, track


@pytest.fixture
def registry() -> Registry:
    return Registry()


def test_percentile_reports_an_observed_value(registry: Registry) -> None:
    # 보간하지 않는다 — 관측되지 않은 수를 보고하지 않기 위해서다.
    for value in (1.0, 2.0, 3.0, 4.0, 100.0):
        registry.record("step", value)

    stats = registry.steps()[0]

    assert stats.percentile(95) in {4.0, 100.0}
    assert stats.p50 == 3.0


def test_tail_is_not_hidden_by_the_average(registry: Registry) -> None:
    # 95건이 5ms, 5건이 2000ms. 평균은 멀쩡해 보이지만 기다리는 사람은 그 5명이다.
    for _ in range(95):
        registry.record("step", 5.0)
    for _ in range(5):
        registry.record("step", 2000.0)

    stats = registry.steps()[0]

    assert stats.p50 == 5.0
    assert stats.percentile(99) == 2000.0


def test_empty_step_reports_zero_not_an_error(registry: Registry) -> None:
    stats = registry.steps()

    assert stats == []


def test_steps_are_ordered_by_total_time(registry: Registry) -> None:
    # 병목이 위에 와야 한다.
    registry.record("빠름", 1.0)
    for _ in range(50):
        registry.record("느림", 10.0)

    assert [stats.name for stats in registry.steps()] == ["느림", "빠름"]


def test_failures_are_counted_separately(registry: Registry) -> None:
    registry.record("step", 1.0, ok=True)
    registry.record("step", 1.0, ok=False)

    assert registry.steps()[0].failures == 1
    assert registry.steps()[0].count == 2


def test_track_records_elapsed_time(registry: Registry) -> None:
    with track("step", registry):
        pass

    assert registry.steps()[0].count == 1


def test_track_records_failure_and_reraises(registry: Registry) -> None:
    with pytest.raises(ValueError), track("step", registry):
        raise ValueError("의도한 실패")

    assert registry.steps()[0].failures == 1


def test_counters_are_sorted_by_size(registry: Registry) -> None:
    registry.increment("적음")
    registry.increment("많음", by=10)

    assert list(registry.counters()) == ["많음", "적음"]


def test_reset_clears_everything(registry: Registry) -> None:
    registry.record("step", 1.0)
    registry.increment("counter")

    registry.reset()

    assert registry.steps() == []
    assert registry.counters() == {}


def test_report_includes_percentile_columns(registry: Registry) -> None:
    registry.record("면책검증", 1.5)

    report = registry.report("본 측정")

    assert "본 측정" in report
    assert "p95" in report
    assert "면책검증" in report
