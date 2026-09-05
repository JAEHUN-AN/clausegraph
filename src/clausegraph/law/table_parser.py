"""괘선 표를 셀 단위로 푼다.

실손의료보험의 면책 사유는 조문 본문이 아니라 표 안에 있다(notes/005).

    ┏━━━━┳━━━━━━━━━━━━━━━━━┓
    ┃보장종목┃보상하지 않는 사항                 ┃
    ┣━━━━╋━━━━━━━━━━━━━━━━━┫
    ┃(1)     ┃  1. 피보험자가 고의로 자신을 해친 경우. 다만, 심신상실┃
    ┃상해급여┃등으로 자유로운 의사결정을 할 수 없는 상태에서        ┃

어려운 지점은 **셀 안에서 한 문장이 여러 줄로 잘려 있다**는 것이다.
셀은 고정 폭(예: 68칸)으로 채워지므로 줄바꿈 자리에 공백이 있었는지가
padding에 흡수돼 사라진다. 두 경우가 실제로 다 나온다.

    '건강보험 임신출산' + '진료비'   -> 공백이 있었다
    '산후기(O00∼'      + 'O99)와'   -> 공백이 없었다

폭만으로는 구별할 수 없어서, **문서 자신을 사전으로 삼아** 판정한다.
같은 표현이 다른 줄·다른 행·다른 시행일자 판본에서는 다른 자리에서
잘리므로, 온전한 형태가 어딘가에는 남아 있다. 그래서 사전은 표 밖 본문이
아니라 **수집한 모든 판본의 전문**으로 만든다. 좁게 잡으면 근거를 못 찾아
'직계혈족'이 '직 계혈족'으로 남는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TOP_BORDER = "┏"
BOTTOM_BORDER = "┗"
ROW_SEPARATOR_CHARS = frozenset("┣╋┫")
CELL_SEPARATOR = "┃"

# 셀 안에 또 다른 표가 들어 있다. 이 줄들은 이어붙이지 않고 줄바꿈으로 남긴다.
NESTED_TABLE_CHARS = frozenset("┌┐└┘├┤┬┴┼─│")

MIN_LEXICON_PROBE = 2

# 경계 주변만 보고 다시 묻을 때의 창 크기
MAX_PROBE_WINDOW = 6
MIN_PROBE_WINDOW = 2
MIN_PROBE_LENGTH = 4

_TOKEN_TAIL_RE = re.compile(r"(\S+)$")
_TOKEN_HEAD_RE = re.compile(r"^(\S+)")


@dataclass(frozen=True)
class TableRow:
    """표의 한 행. 셀은 줄바꿈을 복원해 이어붙인 상태다."""

    cells: tuple[str, ...]


def find_table_blocks(text: str) -> list[list[str]]:
    """`┏`로 열리고 `┗`로 닫히는 덩어리를 잘라낸다."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.split("\n"):
        if line.lstrip().startswith(TOP_BORDER):
            current = [line]
            continue
        if current is None:
            continue
        current.append(line)
        if line.lstrip().startswith(BOTTOM_BORDER):
            blocks.append(current)
            current = None
    return blocks


def parse_table(block: list[str], lexicon: Lexicon) -> list[TableRow]:
    """표 덩어리를 행 목록으로 푼다. 첫 행은 대개 머리글이다."""
    rows: list[TableRow] = []
    pending: list[list[str]] = []

    def flush() -> None:
        if any(any(part.strip() for part in column) for column in pending):
            rows.append(TableRow(cells=tuple(_join_cell(column, lexicon) for column in pending)))

    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        if any(char in ROW_SEPARATOR_CHARS for char in stripped):
            flush()
            pending = []
            continue
        if not stripped.startswith(CELL_SEPARATOR):
            continue

        cells = stripped.split(CELL_SEPARATOR)[1:-1]
        if not pending:
            pending = [[] for _ in cells]
        for index, cell in enumerate(cells[: len(pending)]):
            pending[index].append(cell)

    flush()
    return rows


def _join_cell(lines: list[str], lexicon: Lexicon) -> str:
    """셀 안에서 잘린 줄들을 이어붙인다."""
    joined = ""
    for raw in lines:
        piece = raw.strip()
        if not piece:
            continue
        if not joined:
            joined = piece
            continue
        if _has_nested_table(piece) or _has_nested_table(joined.rsplit("\n", 1)[-1]):
            joined += "\n" + piece
            continue
        joined += ("" if lexicon.is_fused(joined, piece) else " ") + piece
    return joined


def _has_nested_table(line: str) -> bool:
    return any(char in NESTED_TABLE_CHARS for char in line)


class Lexicon:
    """문서 전체를 낱말 사전으로 쓴다.

    표 밖의 본문에서 같은 표현이 어떻게 쓰였는지를 근거로, 줄바꿈 자리에
    공백이 있었는지 판정한다.
    """

    def __init__(self, corpus: str) -> None:
        self._corpus = corpus

    @classmethod
    def from_terms_dir(cls, terms_dir: Path) -> Lexicon:
        """수집한 모든 판본을 이어붙여 사전으로 삼는다."""
        texts = [path.read_text(encoding="utf-8") for path in sorted(terms_dir.glob("*.txt"))]
        if not texts:
            raise FileNotFoundError(f"사전을 만들 약관이 없다: {terms_dir}")
        return cls("\n".join(texts))

    def is_fused(self, left: str, right: str) -> bool:
        """이어붙일 때 공백 없이 붙여야 하는가."""
        tail = _TOKEN_TAIL_RE.search(left)
        head = _TOKEN_HEAD_RE.match(right)
        if tail is None or head is None:
            return False

        left_token, right_token = tail.group(1), head.group(1)
        if len(left_token) + len(right_token) < MIN_LEXICON_PROBE:
            return False

        if left_token + right_token in self._corpus:
            return True
        if f"{left_token} {right_token}" in self._corpus:
            return False

        # 토큰 전체로는 근거를 못 찾는 경우가 있다.
        # '산후기(O00∼' + 'O99)로'은 통째로는 어디에도 없지만, 경계 주변만
        # 보면 'O00∼O99)'가 문서에 있다. 창을 좁혀 가며 다시 묻는다.
        for window in range(MAX_PROBE_WINDOW, MIN_PROBE_WINDOW - 1, -1):
            left, right = left_token[-window:], right_token[:window]
            if len(left) + len(right) < MIN_PROBE_LENGTH:
                continue
            if left + right in self._corpus:
                return True
            if f"{left} {right}" in self._corpus:
                return False

        # 근거가 없으면 띄운다 — 없는 낱말을 만드는 쪽이 더 나쁘다.
        return False
