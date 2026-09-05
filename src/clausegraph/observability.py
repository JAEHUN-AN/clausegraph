"""스텝별 실행 시간을 모아 분위수로 낸다.

공고의 "실행 흐름·비용·지연시간 추적"에 대응한다.

평균은 지연을 재는 데 쓸모가 없다. 심사 100건 중 95건이 5ms이고 5건이
2초라면 평균은 105ms로 멀쩡해 보이지만, 실제로 기다리는 사람은 그 5명이다.
그래서 **p50과 p95를 따로 본다.**

지표는 프로세스 안에 쌓는다. 심사 한 건이 수 ms라 외부 수집기로 보내는
비용이 측정 대상보다 커지고, 폐쇄망 전제라 내보낼 곳도 없다.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from statistics import median


@dataclass
class StepStats:
    """한 스텝의 관측 결과."""

    name: str
    samples: list[float] = field(default_factory=list)
    failures: int = 0

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def total_ms(self) -> float:
        return sum(self.samples)

    def percentile(self, rank: int) -> float:
        """보간하지 않는 최근접 순위 분위수.

        표본이 수십~수백 건이라 보간해도 의미 있는 차이가 없고, 보간하면
        "실제로 관측된 값"이 아닌 수를 보고하게 된다.
        """
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = max(0, min(len(ordered) - 1, round(rank / 100 * len(ordered)) - 1))
        return ordered[index]

    @property
    def p50(self) -> float:
        return median(self.samples) if self.samples else 0.0


class Registry:
    """스텝 이름별로 표본을 모은다."""

    def __init__(self) -> None:
        self._steps: dict[str, StepStats] = {}
        self._counters: dict[str, int] = defaultdict(int)

    def record(self, name: str, elapsed_ms: float, *, ok: bool = True) -> None:
        stats = self._steps.setdefault(name, StepStats(name=name))
        stats.samples.append(elapsed_ms)
        if not ok:
            stats.failures += 1

    def increment(self, name: str, by: int = 1) -> None:
        """가드레일 발동, 판정 종류처럼 세기만 하는 값."""
        self._counters[name] += by

    def steps(self) -> list[StepStats]:
        # 합계가 큰 것부터 — 병목이 위에 온다.
        return sorted(self._steps.values(), key=lambda stats: stats.total_ms, reverse=True)

    def counters(self) -> dict[str, int]:
        return dict(sorted(self._counters.items(), key=lambda item: -item[1]))

    def reset(self) -> None:
        self._steps.clear()
        self._counters.clear()

    def report(self, title: str = "") -> str:
        lines = [f"=== {title} ===" if title else "==="]
        lines.append(
            f"{'스텝':22s} {'건수':>5s} {'p50':>9s} {'p95':>9s} {'p99':>9s} {'합계':>10s}"
        )
        for stats in self.steps():
            lines.append(
                f"{stats.name:22s} {stats.count:5d} "
                f"{stats.p50:8.1f}ms {stats.percentile(95):8.1f}ms "
                f"{stats.percentile(99):8.1f}ms {stats.total_ms:9.0f}ms"
            )
        counters = self.counters()
        if counters:
            lines.append("")
            for name, value in counters.items():
                lines.append(f"  {name:34s} {value}")
        return "\n".join(lines)


# 프로세스 전역 레지스트리. 심사 경로가 하나뿐이라 주입할 이유가 없다.
REGISTRY = Registry()


@contextmanager
def track(name: str, registry: Registry | None = None) -> Iterator[None]:
    """블록의 실행 시간을 잰다. 예외가 나도 실패로 기록하고 다시 던진다."""
    target = registry or REGISTRY
    started = time.perf_counter()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        target.record(name, (time.perf_counter() - started) * 1000, ok=ok)
