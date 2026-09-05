"""라벨러 테스트.

문구는 전부 실제 분쟁조정사례에서 가져왔다. 특히 부정 처리는 이 규칙이
가장 틀리기 쉬운 지점이라 함정 사례를 그대로 고정해 둔다.
"""

from __future__ import annotations

import pytest

from clausegraph.goldset.labeler import (
    Confidence,
    Label,
    is_in_scope,
    label_from_outcome,
    label_from_title,
)
from clausegraph.goldset.models import CaseRef, DisputeCase


def _case(cvpl: str) -> DisputeCase:
    ref = CaseRef(
        case_slno=1, seq=1, rgnl="보험", cvpl=cvpl, title="t", registered_on="2026-01-01"
    )
    return DisputeCase(ref=ref, sections={}, body_text="")


@pytest.mark.parametrize(
    "cvpl,expected",
    [
        ("실손보험(치료비)", True),
        ("질병·상해·간병보험(진단)", True),
        ("생명보험(사망)", True),
        ("보험(일반)", True),
        ("자동차보험(대물)", False),
        ("손해보험(운전자)", False),
    ],
)
def test_scope_excludes_auto_and_general_damage(cvpl: str, expected: bool) -> None:
    assert is_in_scope(_case(cvpl)) is expected


# --- 부정 처리: 같은 술어가 뒤에 붙는 말에 따라 뒤집힌다 ---


def test_unjust_verdict_means_paid() -> None:
    outcome = "보험금을 지급하지 않은 업무처리는 부당하다고 판단"

    assert label_from_outcome(outcome)[0] == Label.PAID


def test_negated_verdict_means_denied() -> None:
    outcome = "보험금을 지급하지 않은 업무처리가 부당하다고 보기 어렵다고 판단"

    assert label_from_outcome(outcome)[0] == Label.DENIED


def test_negation_after_positive_verdict_flips_result() -> None:
    # caseSlno 14 — '부당하다고 판단'을 앞에서 잡으면 PAID로 뒤집힌다.
    outcome = "관련 비용을 지급하지 아니한 업무처리를 부당하다고 판단하기는 어려움을 안내"

    assert label_from_outcome(outcome)[0] == Label.DENIED


def test_negation_after_pay_order_flips_result() -> None:
    # caseSlno 15 — '지급하도록'만 보면 PAID로 뒤집힌다.
    outcome = "질병입원일당 보험금을 지급하도록 권고하기 어려움을 안내"

    assert label_from_outcome(outcome)[0] == Label.DENIED


def test_pay_order_without_negation_means_paid() -> None:
    # caseSlno 125
    outcome = "‘영구적’ 장해로 보아 관련 보험금을 지급할 필요가 있다고 판단"

    assert label_from_outcome(outcome)[0] == Label.PAID


def test_contract_action_is_not_a_claim_decision() -> None:
    outcome = "고지의무 위반으로 보험계약을 해지한 업무처리가 부당하다고 보기 어려움"

    assert label_from_outcome(outcome)[0] == Label.NOT_CLAIM


def test_additional_documents_requested_is_its_own_outcome() -> None:
    # caseSlno 78 — 지급 여부가 아직 정해지지 않았다.
    outcome = "보험회사가 확인이 가능한 추가 서류 제출을 요구하는 것이 부당하지 않음을 안내"

    assert label_from_outcome(outcome)[0] == Label.NEEDS_DOCS


def test_empty_outcome_is_unknown() -> None:
    assert label_from_outcome("   ") == (Label.UNKNOWN, "")


def test_evidence_quote_is_returned_with_label() -> None:
    label, evidence = label_from_outcome("보험금을 지급하지 않은 업무처리는 부당하다고 판단")

    assert label == Label.PAID
    assert "부당" in evidence


# --- 제목 신호 ---


def test_title_takes_the_last_signal() -> None:
    # caseSlno 151 — 앞의 '지급 거절'이 아니라 뒤의 '지급받은'이 결론이다.
    title = "상해사망보험금 지급 거절 후 재검토를 거쳐 지급받은 사례"

    assert label_from_title(title) == Label.PAID


def test_partial_marker_wins_over_position() -> None:
    # caseSlno 18 — '감액된'은 결론이 아니라 지급 형태를 말하는 수식어다.
    title = "보장 개시일이 지났더라도 보험약관에 따라 감액된 보험금을 지급받은 사례"

    assert label_from_title(title) == Label.PARTIAL


def test_contract_topic_title_is_not_a_claim() -> None:
    title = "보험료 미납으로 보험계약이 해지된 사례"

    assert label_from_title(title) == Label.NOT_CLAIM


def test_unmatched_title_is_unknown() -> None:
    assert label_from_title("말하는 기능 장해 관련 분쟁") == Label.UNKNOWN


# --- 두 신호 결합 ---


def test_agreeing_signals_give_high_confidence() -> None:
    ref = CaseRef(
        case_slno=1,
        seq=1,
        rgnl="보험",
        cvpl="실손보험(치료비)",
        title="실손보험금을 지급받지 못한 사례",
        registered_on="2026-01-01",
    )
    case = DisputeCase(
        ref=ref,
        sections={"처리결과": "보험금을 지급하지 않은 업무처리가 부당하다고 보기 어렵다고 판단"},
        body_text="",
    )

    from clausegraph.goldset.labeler import label_case

    result = label_case(case)

    assert result.label == Label.DENIED
    assert result.confidence == Confidence.HIGH
