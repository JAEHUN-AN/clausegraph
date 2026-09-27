"""요청 단위 추적 — 한 건이 왜 그렇게 끝났는지 되짚는다.

[notes/037](../../notes/037-jd-gap.md)에서 관측은 **부분**이었다. p50/p95와
카운터는 있는데 **요청 단위 상관 ID가 없어서**, 집계로 답할 수 있는 질문과
없는 질문이 갈렸다.

| 집계로 답할 수 있다 | 집계로 못 답한다 |
|---|---|
| HUMAN_REVIEW가 몇 건인가 | **이** 건이 왜 HUMAN_REVIEW인가 |
| 면책검증 p95가 얼마인가 | 느렸던 그 건에서 어느 스텝이 느렸나 |
| 가드레일이 몇 번 발동했나 | 이 판정을 바꾼 가드레일이 무엇인가 |
| — | 그때 적용된 판본과 스코프가 무엇이었나 |

오른쪽은 심사자가 실제로 묻는 질문이고, 왼쪽만으로는 하나도 답할 수 없다.

## 프로세스 안에 둔다

notes/011에서 지표를 밖으로 안 보낸 이유가 그대로 적용된다 — 심사 한 건이
0.3ms라 외부 수집기로 보내는 비용이 측정 대상보다 크고, 폐쇄망 전제라
내보낼 곳도 없다. 대신 **최근 것만 링 버퍼로** 들고 있는다. 전부 들고
있으면 오래 도는 프로세스가 메모리를 먹는다.

## 스팬이 트리인 이유

스텝이 평평한 리스트면 "면책검증이 70ms"까지만 말한다. 그 안에서 열거가
느린지 문서빈도 계산이 느린지는 못 가른다 — notes/011에서 그걸 찾느라
코드를 읽어야 했다. 부모를 들고 있으면 그 질문이 조회가 된다.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# 들고 있을 트레이스 수. 넘으면 오래된 것부터 버린다.
MAX_TRACES = 256


@dataclass
class Span:
    """스텝 하나. 시작과 끝, 그리고 왜 그랬는지."""

    name: str
    depth: int
    started_ms: float
    elapsed_ms: float = 0.0
    ok: bool = True
    # 그 스텝이 무엇을 보고 무엇을 정했는지. 판정을 되짚을 때 쓴다.
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass
class Trace:
    """청구 한 건의 실행 기록."""

    trace_id: str
    subject: str = ""
    spans: list[Span] = field(default_factory=list)
    # 판정을 바꾼 것. 가드레일 이름과 바뀐 방향.
    verdict: str = ""
    outcome: str = ""

    @property
    def total_ms(self) -> float:
        return sum(span.elapsed_ms for span in self.spans if span.depth == 0)

    def render(self) -> str:
        """한 건을 사람이 읽을 수 있게.

        **집계 보고서와 다른 것을 보여 줘야 한다.** 같은 수를 다시 적을
        것이면 트레이스가 있을 이유가 없다. 여기서는 순서·중첩·각 스텝이
        무엇을 보고 정했는지를 낸다.
        """
        lines = [f"trace {self.trace_id}  {self.subject}  총 {self.total_ms:.2f}ms"]
        for span in self.spans:
            mark = " " if span.ok else "!"
            indent = "  " * span.depth
            lines.append(
                f" {mark} {indent}{span.name:<24} {span.elapsed_ms:8.2f}ms"
                + (f"  {_render_attrs(span.attributes)}" if span.attributes else "")
            )
        if self.verdict:
            lines.append(f"   판정: {self.verdict}")
        return "\n".join(lines)


def _render_attrs(attributes: dict[str, object]) -> str:
    return " ".join(f"{key}={value}" for key, value in attributes.items())


class TraceStore:
    """최근 트레이스를 id로 찾을 수 있게 들고 있는다."""

    def __init__(self, limit: int = MAX_TRACES) -> None:
        self._traces: OrderedDict[str, Trace] = OrderedDict()
        self._limit = limit

    def put(self, trace: Trace) -> None:
        self._traces[trace.trace_id] = trace
        self._traces.move_to_end(trace.trace_id)
        while len(self._traces) > self._limit:
            self._traces.popitem(last=False)

    def get(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def recent(self, count: int = 10) -> list[Trace]:
        return list(self._traces.values())[-count:]

    def matching(self, predicate) -> list[Trace]:
        """조건에 맞는 트레이스. "HUMAN_REVIEW인 것만" 같은 질문에 쓴다."""
        return [trace for trace in self._traces.values() if predicate(trace)]

    def clear(self) -> None:
        self._traces.clear()

    def __len__(self) -> int:
        return len(self._traces)


TRACES = TraceStore()

# 지금 어느 트레이스 안에 있는가. 스텝 함수가 인자를 하나 더 받지 않아도
# 되게 하는 장치다 — 인자로 넘기면 스텝을 부르는 모든 곳을 고쳐야 하고,
# 고치다 한 곳을 빠뜨리면 그 스텝만 트레이스에서 사라진다.
_CURRENT: ContextVar[Trace | None] = ContextVar("clausegraph_trace", default=None)
_DEPTH: ContextVar[int] = ContextVar("clausegraph_depth", default=0)


def current() -> Trace | None:
    return _CURRENT.get()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def trace(subject: str, *, trace_id: str | None = None) -> Iterator[Trace]:
    """한 건을 추적한다. 끝나면 저장소에 넣는다."""
    record = Trace(trace_id=trace_id or new_trace_id(), subject=subject)
    token = _CURRENT.set(record)
    depth_token = _DEPTH.set(0)
    try:
        yield record
    finally:
        _CURRENT.reset(token)
        _DEPTH.reset(depth_token)
        TRACES.put(record)


@contextmanager
def span(name: str, **attributes: object) -> Iterator[Span]:
    """스텝 하나를 잰다. 트레이스 밖이면 아무것도 하지 않는다.

    **밖에서도 터지지 않아야 한다.** 스텝 함수는 평가·적재 같은 데서도
    불리는데, 거기서 트레이스를 열지 않았다고 예외가 나면 추적을 넣은 것이
    시스템을 더 약하게 만든 셈이 된다.
    """
    record = current()
    if record is None:
        yield Span(name=name, depth=0, started_ms=0.0)
        return

    depth = _DEPTH.get()
    entry = Span(
        name=name,
        depth=depth,
        started_ms=time.perf_counter() * 1000,
        attributes=dict(attributes),
    )
    record.spans.append(entry)
    depth_token = _DEPTH.set(depth + 1)
    started = time.perf_counter()
    try:
        yield entry
    except Exception:
        entry.ok = False
        raise
    finally:
        entry.elapsed_ms = (time.perf_counter() - started) * 1000
        _DEPTH.reset(depth_token)
