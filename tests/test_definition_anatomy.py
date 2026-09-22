"""DEFINITION 라벨 파일을 지킨다.

손으로 붙인 라벨은 조용히 어긋난다. 사례를 더 수집하면 DEFINITION이 늘고,
라벨을 안 붙이면 **그 사례는 통계에서 빠진 채** 비율만 그럴듯하게 남는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clausegraph.goldset.definition_anatomy import (
    NEEDS,
    definition_slnos,
    load_gold,
)

GOLDSET = Path("data/goldset")

pytestmark = pytest.mark.skipif(
    not (GOLDSET / "definition_gold.jsonl").exists(),
    reason="라벨 파일이 없다",
)


def test_every_definition_case_is_labeled() -> None:
    gold = {item.case_slno for item in load_gold(GOLDSET)}

    assert gold == definition_slnos(GOLDSET)


def test_verdicts_are_binary() -> None:
    assert {item.verdict for item in load_gold(GOLDSET)} <= {"IN", "OUT"}


def test_needs_come_from_the_fixed_set() -> None:
    # 오타로 만든 새 범주가 통계에서 1건짜리 항목으로 조용히 늘어나는 것을 막는다.
    used = {name for item in load_gold(GOLDSET) for name in item.needs}

    assert used <= set(NEEDS)


def test_every_case_says_what_it_needed() -> None:
    assert all(item.needs for item in load_gold(GOLDSET))


def test_every_case_carries_its_reason() -> None:
    # 근거 없는 라벨은 나중에 못 되짚는다.
    assert all(item.note.strip() for item in load_gold(GOLDSET))


def test_the_sample_is_too_skewed_for_accuracy() -> None:
    """쏠림을 테스트로 박아 둔다.

    사례가 늘어 균형이 잡히면 이 테스트가 깨진다. 그때가 정확도를 지표로
    쓸 수 있게 되는 시점이고, notes/034의 결론을 다시 읽어야 하는 시점이다.
    """
    gold = load_gold(GOLDSET)
    majority = max(
        sum(1 for item in gold if item.verdict == value) for value in ("IN", "OUT")
    )

    assert majority / len(gold) > 0.8
