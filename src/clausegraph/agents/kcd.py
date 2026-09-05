"""면책 조항에 적힌 질병분류 코드 범위를 읽는다.

약관은 면책 대상을 코드로 못박는다.

    정신 및 행동장애(F04∼F99)
    비만(E66)
    요실금(N39.3, N39.4, R32)
    임신, 출산(제왕절개를 포함합니다), 산후기(O00∼O99)

이 표기를 파싱해 청구의 진단코드와 대조하면 **결정론적으로** 걸린다.
낱말 유사도로 맞히는 것보다 정확하고, 왜 걸렸는지 설명할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 물결표가 문서마다 다르다 — ∼(U+223C), ~(U+007E), ～(U+FF5E)
_RANGE_RE = re.compile(r"([A-Z])(\d{2})(?:\.\d{1,2})?\s*[∼~～-]\s*([A-Z])(\d{2})(?:\.\d{1,2})?")
_SINGLE_RE = re.compile(r"\b([A-Z])(\d{2})(?:\.(\d{1,2}))?\b")


@dataclass(frozen=True)
class CodeRange:
    """[start, end] 구간. 단일 코드면 두 끝이 같다."""

    letter: str
    start: int
    end: int
    subdivision: str | None = None

    def contains(self, code: str) -> bool:
        match = _SINGLE_RE.fullmatch(code.strip())
        if not match:
            return False
        letter, number, subdivision = match.group(1), int(match.group(2)), match.group(3)
        if letter != self.letter or not (self.start <= number <= self.end):
            return False
        # 약관이 세분류까지 못박았다면(N39.3) 그 세분류만 걸린다.
        if self.subdivision is not None:
            return subdivision == self.subdivision
        return True


def parse_code_ranges(text: str) -> tuple[CodeRange, ...]:
    """조항 문구에서 코드 범위를 뽑는다."""
    ranges: list[CodeRange] = []
    consumed: list[tuple[int, int]] = []

    for match in _RANGE_RE.finditer(text):
        start_letter, start_number, end_letter, end_number = match.groups()
        if start_letter != end_letter:
            # A00∼B99처럼 글자가 다른 범위는 다루지 않는다 — 조용히 넘기지 않고 건너뛴다.
            continue
        ranges.append(CodeRange(start_letter, int(start_number), int(end_number)))
        consumed.append(match.span())

    for match in _SINGLE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        letter, number, subdivision = match.group(1), int(match.group(2)), match.group(3)
        ranges.append(CodeRange(letter, number, number, subdivision))

    return tuple(ranges)


def matches(text: str, codes: tuple[str, ...]) -> tuple[str, ...]:
    """조항이 못박은 범위에 걸리는 진단코드들."""
    ranges = parse_code_ranges(text)
    if not ranges:
        return ()
    return tuple(code for code in codes if any(rng.contains(code) for rng in ranges))
