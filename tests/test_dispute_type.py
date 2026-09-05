"""Tier B 분쟁 유형 라벨러 — 실제 쟁점 문장으로 고정한다.

아래 문장은 전부 금감원 분쟁조정사례의 쟁점 원문이다. 지어낸 예시로
규칙을 고정하면 규칙이 데이터가 아니라 예시에 맞춰진다.
"""

from __future__ import annotations

import pytest

from clausegraph.goldset.dispute_type import (
    HANDLED,
    DisputeType,
    classify_issue,
)


@pytest.mark.parametrize(
    "issue,expected",
    [
        # 정의 해석 — 이 시스템이 못 푸는 자리
        (
            "IPL시술이 약관이 정하는 수술의 정의와 범위에 포함되는지 여부",
            DisputeType.DEFINITION,
        ),
        (
            "피보험자가 받은 케모포트삽입술이 본건 보험약관에서 정하는 “수술”에 해당되는지 여부",
            DisputeType.DEFINITION,
        ),
        (
            "법정전염병인 코로나19가 약관상 진단보험금 지급 대상인 특정전염병에 해당하는지 여부",
            DisputeType.DEFINITION,
        ),
        # 면책 — 약관이 면책이라고 이름 붙인 자리
        (
            "치료목적으로 사용된 압박고정용 재료대가 실손보험에서 보상하지 않는 사항으로 "
            "정하고 있는 ‘보조기 등’에 해당하는지 여부",
            DisputeType.EXCLUSION,
        ),
        # 술어는 일반형인데 주제어가 면책 목록에 있다
        (
            "한방병원에서 받은 비급여 항목에 대한 치료비가 「실손의료보험 약관」에서 "
            "정한 보험금 지급대상에 해당하는지 여부",
            DisputeType.EXCLUSION,
        ),
        (
            "예방목적의 건강검진, 백신 접종 및 증명서 발급 비용 등이 "
            "실손의료보험금의 보상 대상에 해당하는지 여부",
            DisputeType.EXCLUSION,
        ),
        # 시점
        (
            "실손보험 면책기간 중 발생한 통원의료비 보상 여부",
            DisputeType.TIMING,
        ),
        (
            "단체보험의 피보험자가 소속 회사에서 퇴직한 이후에도 진단보험금의 "
            "지급 대상이 되는지 여부",
            DisputeType.TIMING,
        ),
        # 중복보상
        (
            "국민건강보험공단이 부담하는 본인부담상한액 초과액(=환급금)이 "
            "실손의료보험금 지급 대상에 포함되는지 여부",
            DisputeType.DUPLICATE,
        ),
        # 고지·통지
        (
            "고등학교를 졸업하지 않은 상태에서 취업한 경우에도 보험회사에 "
            "직업 변경사실을 통지할 의무가 발생하는지 여부",
            DisputeType.DISCLOSURE,
        ),
        # 입증
        (
            "신청인이 농업작업 중에 허리를 다쳤는지를 충분히 입증하였는지 여부",
            DisputeType.PROOF,
        ),
        # 청구 절차
        (
            "피보험자가 의사능력이 없는 경우 가족이 피보험자를 대신하여 "
            "보험금을 청구할 수 있는지 여부",
            DisputeType.PROCEDURE,
        ),
        # 담보 미가입
        (
            "「비급여 주사료 특약」을 가입하지 않은 경우 ’관절강내 무릎 주사치료‘를 "
            "보상받을 수 있는지 여부",
            DisputeType.SCOPE,
        ),
    ],
)
def test_real_issues_are_classified(issue: str, expected: DisputeType) -> None:
    assert classify_issue(issue)[0] is expected


# --- 손으로 검수하다 잡은 것들 ---


def test_exclusion_topic_does_not_swallow_a_disclosure_dispute() -> None:
    # '건강검진'이 면책 주제어지만 이 쟁점은 계약 전 알릴 의무다.
    issue = (
        "단순 건강검진 결과 받은 의심소견(이상소견)도 모두 "
        "계약전 알릴의무 이행 대상에 포함되는지 여부"
    )

    assert classify_issue(issue)[0] is DisputeType.DISCLOSURE


def test_exclusion_topic_needs_a_real_indemnity_context() -> None:
    # '치과치료'가 면책 주제어지만 여기서는 치과 담보의 지급사유 문제다.
    # 실손 문맥이 없으면 면책으로 보지 않는다.
    issue = (
        "이빨의 뿌리를 제거한 자리에 임플란트를 삽입하기 위한 시술이 "
        "약관에서 정하고 있는 치과치료보험금 지급 사유에 해당하는지 여부"
    )

    assert classify_issue(issue)[0] is DisputeType.DEFINITION


def test_premium_refund_is_not_a_duplicate_indemnity_dispute() -> None:
    # 보험료 환급은 계약 문제이지 다른 제도에서 받은 의료비가 아니다.
    issue = "건강(우량)체 적용 후 정산받은 보험료 환급금액이 적정한지 여부"

    assert classify_issue(issue)[0] is DisputeType.CONTRACT


# --- 모르는 것은 모른다고 한다 ---


@pytest.mark.parametrize(
    "issue",
    [
        "",
        "   ",
        "입원 수술보험금 지급 관련 업무처리가 적정한지 여부",
    ],
)
def test_unclassifiable_issue_is_not_forced(issue: str) -> None:
    # 억지로 배정하면 범위 측정이 바로 거짓이 된다.
    assert classify_issue(issue)[0] is DisputeType.UNKNOWN


def test_evidence_quote_is_returned_for_a_match() -> None:
    dispute_type, evidence = classify_issue(
        "실손보험 면책기간 중 발생한 통원의료비 보상 여부"
    )

    assert dispute_type is DisputeType.TIMING
    assert "면책기간" in evidence


def test_every_type_declares_whether_it_is_handled() -> None:
    # 유형을 더하고 범위 표를 안 고치면 범위 계산이 조용히 틀린다.
    assert set(HANDLED) == set(DisputeType)


def test_definition_disputes_are_not_claimed_as_handled() -> None:
    # 가장 큰 덩어리이고, 이 시스템은 못 푼다. 이걸 handled로 바꾸면
    # 범위 진술 전체가 거짓이 된다.
    assert HANDLED[DisputeType.DEFINITION] is False
    assert HANDLED[DisputeType.UNKNOWN] is False
