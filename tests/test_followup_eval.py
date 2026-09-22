"""후속 질문 평가셋 자체를 지킨다.

평가셋은 코드보다 조용히 썩는다. 분류를 하나 늘려 놓고 문항을 안 늘리면,
그 분류는 **한 번도 재지 않은 채로** 정확도 100%에 묻힌다.
"""

from __future__ import annotations

from clausegraph.agents.followup import FollowUp
from clausegraph.goldset.followup_eval import WRITTEN, score_written


def test_every_category_has_cases() -> None:
    # 분류를 늘리면 문항도 늘려야 한다. 안 그러면 새 분류는 재지 않은 채
    # 전체 정확도 뒤에 숨는다.
    covered = {case.expected for case in WRITTEN}

    assert covered == set(FollowUp)


def test_the_dangerous_pair_is_in_the_set() -> None:
    """'왜'가 섞인 새 사실은 이 평가의 존재 이유다.

    이 문항이 빠지면 낡은 판정을 설명하는 회귀를 평가가 못 잡는다.
    """
    questions = [case.question for case in WRITTEN if case.expected is FollowUp.NEW_FACTS]

    assert any("왜" in question for question in questions)


def test_lookalikes_are_in_the_set() -> None:
    # 새 사실처럼 보이지만 아닌 것 — 이미 아는 값을 되물은 말. 이게 없으면
    # "전부 NEW_FACTS로 보내기"가 만점을 받는다.
    lookalikes = [
        case
        for case in WRITTEN
        if case.expected is not FollowUp.NEW_FACTS and any(ch.isdigit() for ch in case.question)
    ]

    assert len(lookalikes) >= 3


def test_written_set_passes() -> None:
    # 여기 적힌 수치는 **규칙을 이 표본에 맞춰 고친 뒤**의 것이다.
    # 독립적인 수가 아니며, 그 사정은 notes/033에 적었다.
    result = score_written()

    assert result.correct == result.total
    assert result.new_facts_missed == 0
    assert result.new_facts_false == 0
