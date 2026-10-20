"""feat/overseas-payout 2차 반박 검증 반영 (2026-10-21).

검토 반영 커밋(8a20414)을 세 관점(과다지급 사냥 / main 대비 회귀 / 수식어 오탐)으로
다시 깨 보니 지급 쪽으로 새는 길이 더 있었다. 원칙 하나로 막았다 —
**확실하지 않은 코드는 버리지도 확정하지도 않고 불확실 면책으로 사람에게 보낸다.**
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents.amount_rules import coverage_kinds, find_rule, why_no_rule
from clausegraph.agents.exclusion import _reason_code_hit
from clausegraph.agents.extract import codes_in, extract_claim, parse_codes
from clausegraph.agents.kcd import matches
from clausegraph.kcd.index import Disease, KcdIndex

OVERSEAS = "기본형 해외여행 실손의료보험"
BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
ENROLLED = date(2026, 7, 1)


def _documented():
    return extract_claim(
        "C", OVERSEAS, ENROLLED, "해외 병원 통원 치료",
        diagnosis_codes=("S62.0", "F20"), claimed_amount=2_500_000,
        sum_insured=10_000_000, treated_abroad=True,
    )


# --- OP-1: 재심사의 빈 코드 표기는 "주지 않았다" ---


@pytest.mark.parametrize("empty", ["[]", ",", '""', "''", "[ ]", ";", '[""]', "·"])
def test_empty_code_notation_in_a_revision_keeps_the_documented_codes(empty) -> None:
    from clausegraph.mcp_server.server import _revise

    revised = _revise(_documented(), -1, -1, -1, -1, "", diagnosis_codes=empty)

    assert "F20" in revised.diagnosis_codes


# --- OP-6: 공백 서술은 서술을 준 것이 아니다 ---


@pytest.mark.parametrize("blank", [" ", "\n", "\t ", "  \n  "])
def test_blank_narrative_keeps_what_the_old_narrative_said(blank) -> None:
    from clausegraph.mcp_server.server import _revise

    claim = extract_claim("C", BENEFIT, ENROLLED, "F20 기왕증, 3일간 입원 진료비 100만원")
    revised = _revise(claim, -1, -1, -1, -1, blank)

    assert "F20" in revised.diagnosis_codes
    assert revised.hospital_days == 3


# --- OP-2: 세분류 면책은 그 아래까지 ---


@pytest.mark.parametrize("code,hit", [
    ("N39.4", True), ("N39.40", True), ("N39.48", True), ("N39.3", True),
    ("N39", False), ("N39.5", False), ("R32", True),
])
def test_subdivision_exclusion_covers_its_children(code, hit) -> None:
    assert bool(matches("요실금(N39.3, N39.4, R32)", (code,))) is hit


def test_dotless_five_character_code_reaches_the_subdivision_exclusion() -> None:
    assert matches("요실금(N39.3, N39.4, R32)", parse_codes(("N3940",))) == ("N39.40",)


# --- OP-3: 사유 자체를 말하는 코드 ---


@pytest.mark.parametrize("clause,codes,expected", [
    ("피보험자가 고의로 자신을 해친 경우. 다만 …", ("S61.0", "X78"), "X78"),
    ("전쟁, 외국의 무력행사, 혁명, 내란, 사변, 폭동", ("S31.1", "Y36.2"), "Y36.2"),
    ("건강검진(단, …), 예방접종, 인공유산에 든 비용", ("Z00.0",), "Z00.0"),
    ("건강검진(단, …), 예방접종, 인공유산에 든 비용", ("Z23.5",), "Z23.5"),
    ("외모개선 목적의 치료로 인하여 발생한 의료비", ("Z41.1",), "Z41.1"),
])
def test_reason_codes_point_at_their_exclusion(clause, codes, expected) -> None:
    hit = _reason_code_hit(clause, codes)

    assert hit is not None and hit[0] == expected


def test_reason_code_does_not_fire_on_an_unrelated_exclusion() -> None:
    assert _reason_code_hit("정신 및 행동장애(F04∼F99)", ("X78",)) is None
    assert _reason_code_hit("피보험자가 고의로 자신을 해친 경우", ("W54.9", "S61.9")) is None


# --- OP-5: 서술 속 코드는 조사가 붙어도, 점이 없어도 ---


@pytest.mark.parametrize("narrative,expected", [
    ("기왕 진단 F200 환자", ("F20.0",)),
    ("2년 전 F20으로 진단받은 환자", ("F20",)),
    ("기왕증 F32를 앓던 중", ("F32",)),
    ("기왕진단F20 환자", ("F20",)),
    ("소문자 f20 표기", ("F20",)),
    ("S62.0 손목 골절", ("S62.0",)),
])
def test_codes_in_the_narrative_are_read_with_particles(narrative, expected) -> None:
    assert codes_in(narrative) == expected


@pytest.mark.parametrize("narrative", ["E-9 비자", "COVID19 검사", "2026년 8월 12일", "USD 1,800"])
def test_codes_in_the_narrative_do_not_come_from_other_tokens(narrative) -> None:
    assert codes_in(narrative) == ()


# --- OP-7·OP-8: 계산하면 안 되는 경우는 이유를 말한다 ---


def test_domestic_product_does_not_price_overseas_treatment() -> None:
    assert find_rule(BENEFIT, None, ("S62.0",), abroad=True) is None
    assert "국내 상품 산식" in why_no_rule(BENEFIT, ("S62.0",), abroad=True)


@pytest.mark.parametrize("codes,mixed", [
    (("S62.0", "J18"), True),
    (("S61.9", "W54.9"), False),   # 외인 코드는 질병이 아니다
    (("S62.0", "R52"), False),     # 증상 코드는 질병으로 세지 않는다
    (("F41.0", "R00.2"), False),
])
def test_mixed_injury_and_disease_cannot_share_one_sum_insured(codes, mixed) -> None:
    assert (len(coverage_kinds(codes)) > 1) is mixed
    rule = find_rule(OVERSEAS, None, codes, abroad=True)
    if mixed:
        assert rule is None
        assert "섞여" in why_no_rule(OVERSEAS, codes, abroad=True)
    else:
        assert rule is not None


# --- OP-9: 전각·가운뎃점 ---


def test_fullwidth_and_middle_dot_notation_is_parsed() -> None:
    assert parse_codes(("Ｆ２０",)) == ("F20",)
    assert parse_codes(("F20ㆍS62.0",)) == ("F20", "S62.0")


# --- REG-1·2: 확실하지 않은 코드는 약한 코드로 ---


def _index(*rows):
    return KcdIndex([Disease(code=c, name=n, english="", complete=True) for c, n in rows])


def test_glued_short_key_becomes_a_weak_code_not_nothing() -> None:
    index = _index(("E66", "비만"))

    assert index.lookup("검사비만 청구합니다") == ()
    assert index.lookup_weak("검사비만 청구합니다") == ("E66",)


def test_weak_codes_reach_the_claim_but_not_its_diagnosis_codes() -> None:
    claim = extract_claim(
        "C", BENEFIT, ENROLLED, "검사비만 청구합니다",
        enrich=lambda _: (), weak=lambda _: ("E66",),
    )

    assert claim.diagnosis_codes == ()
    assert claim.weak_codes == ("E66",)


def test_documented_codes_turn_the_dictionary_guess_into_weak_codes() -> None:
    # 서류 코드가 있으면 사전 추정은 판정 코드가 아니지만, 버리지도 않는다.
    claim = extract_claim(
        "C", OVERSEAS, ENROLLED, "손목 골절", enrich=lambda _: ("F05",),
        diagnosis_codes=("S62.0",),
    )

    assert claim.diagnosis_codes == ("S62.0",)
    assert claim.weak_codes == ("F05",)


@pytest.mark.parametrize(
    "narrative", ["복부유착 박리술", "고도유착", "복부가스 팽만", "교원양성기관"]
)
def test_homonym_and_non_disease_keys_do_not_produce_codes(narrative) -> None:
    index = _index(("K00", "유착"), ("T59", "가스"), ("R89", "기관"))

    assert index.lookup(narrative) == ()
    assert index.lookup_weak(narrative) == ()
