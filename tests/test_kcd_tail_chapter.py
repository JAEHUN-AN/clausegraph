"""장을 정하지 못한 tail 열쇠 — 확실 코드가 아니라 약한 코드 (notes/047).

tail 열쇠는 병명에서 앞말을 떼어 낸 마지막 낱말이다. '임신중 … 마취제 … 산모합병증'의
`마취제`가 O29를, '분만에 따른 요로감염'의 `요로감염`이 O86을 확실 코드로 물어 와
임신·출산(O00∼O99) 면책이 **확실 면책 -> 부지급**으로 걸렸다(2026-10-21 실측).
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents.extract import extract_claim
from clausegraph.agents.kcd import matches
from clausegraph.kcd.index import DEFAULT_CSV, Disease, KcdIndex, _read_tail, load_index

BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
ENROLLED = date(2026, 7, 1)
PREGNANCY_EXCLUSION = "임신, 출산(제왕절개를 포함합니다), 산후기(O00∼O99)"
MENTAL_EXCLUSION = "정신 및 행동장애(F04∼F99)"
DENTAL_EXCLUSION = "치과치료(K00∼K08)"
EXCLUSIONS = (PREGNANCY_EXCLUSION, MENTAL_EXCLUSION, DENTAL_EXCLUSION)

# 상병마스터의 실제 병명이다.
_SEDATIVE_COMPLICATION = "마취제, 진통제 또는 기타 진정제 투여로 인해 발생한 산모합병증"
_ROWS = (
    ("O29", f"임신중 전신 또는 국소 {_SEDATIVE_COMPLICATION}"),
    ("O74", f"진통 및 분만중 전신 또는 국소 {_SEDATIVE_COMPLICATION}"),
    ("N390", "부위가 명시되지 않은 요로감염"),
    ("O862", "분만에 따른 요로감염"),
    ("K0762", "턱관절의 재발성 탈구 및 아탈구"),
    ("S0320", "치아의 아탈구"),
    ("B653", "수영자 가려움증"),
    ("F458", "심인성 가려움증"),
    ("O834", "분만을 용이하게 하기 위한 개두술"),
    ("O218", "임신에 합병된, 달리 분류된 질환으로 인한 구토"),
    ("J189", "상세불명의 폐렴"),
    ("O450", "무피브리노젠혈증과 관련된 (과다)출혈을 동반한 태반조기박리"),
    ("E05", "갑상선(갑상선독증) 심장질환(I43.8*)"),
    ("I27", "기타 폐성 심장질환"),
    ("A079", "상세불명의 원충성 장질환"),
    ("K052", "급성 치주염"),
    ("O68", "약물투여로 인한 진통 또는 분만 중 태아절박"),
)


def _index() -> KcdIndex:
    return KcdIndex([Disease(code=c, name=n, english="", complete=True) for c, n in _ROWS])


@pytest.mark.parametrize(
    "narrative,code",
    [
        ("수술 전 마취제 투여 후 무릎 관절경 수술", "O29"),
        ("요로감염으로 3일 입원", "O86"),
        ("폐렴이 합병된 상태로 입원", "O21"),
        ("뇌출혈로 개두술 시행", "O83"),
        ("어깨 아탈구로 정형외과 통원", "K07"),
        ("가려움증으로 피부과 통원", "F45"),
        # 맥락 낱말에서 뺀 것 — '진통'(鎭痛제), '유산'(유산소·유산균).
        ("진통제 복용 후 요로감염", "O86"),
        ("유산소 운동 후 요로감염", "O86"),
        ("유산균 복용 후 요로감염", "O86"),
        # 맥락 낱말이 다른 말에 걸린 것 (검토 MEDIUM-1).
        ("태아보험 가입한 아이가 요로감염으로 입원", "O86"),
        ("요로감염으로 3일 입원, 급여 부분만 청구합니다", "O86"),
        ("수술 후 30분만에 요로감염", "O86"),
        ("생산모델 조립 중 요로감염", "O86"),
        ("양수인 변경 후 요로감염", "O86"),
    ],
)
def test_a_tail_that_cannot_decide_its_chapter_is_only_a_weak_code(narrative, code) -> None:
    index = _index()

    assert code not in index.lookup(narrative)
    assert code in index.lookup_weak(narrative)


@pytest.mark.parametrize(
    "narrative,code",
    [
        # O로 읽지 않으면 남는 장이 하나다 — 그 코드는 확실하다.
        ("요로감염으로 3일 입원", "N39"),
        ("폐렴이 합병된 상태로 입원", "J18"),
        # 임신 맥락이 있으면 O로 읽는다.
        ("임신 32주 요로감염으로 입원", "O86"),
        ("분만 후 요로감염", "O86"),
        ("임신 중 마취제 부작용", "O29"),
        ("자연분만 후 요로감염", "O86"),
        # 열쇠 자신이 임신 맥락이다.
        ("태반조기박리로 응급 수술", "O45"),
        ("태아절박으로 응급 수술", "O68"),
        # 임신 맥락은 O를 가리키는 tail만 바꾼다 — 치주염(K05)은 그대로다.
        ("임신 중 치주염으로 치과 치료", "K05"),
    ],
)
def test_a_tail_whose_chapter_is_decided_stays_certain(narrative, code) -> None:
    assert code in _index().lookup(narrative)


@pytest.mark.parametrize(
    "codes",
    [("O29",), ("O29", "O74"), ("N39", "O08", "O86"), ("K07", "S03"), ("A56", "N72", "O86"),
     ("F33", "F34"), ("O04", "P96")],
)
@pytest.mark.parametrize("text", ["요로감염으로 입원", "임신 중 요로감염으로 입원"])
def test_reading_a_tail_only_splits_its_codes(codes, text) -> None:
    # 버리지도 만들지도 않는다 — 확실과 약한으로 나눌 뿐이다. 그래서 main 대비
    # 코드가 사라지거나 새로 생길 수 없다.
    sure, unsure = _read_tail(codes, text)

    assert set(sure) | set(unsure) == set(codes)
    assert not set(sure) & set(unsure)


def test_pregnancy_context_moves_the_other_chapter_to_weak() -> None:
    index = _index()

    assert "N39" not in index.lookup("임신 32주 요로감염으로 입원")
    assert "N39" in index.lookup_weak("임신 32주 요로감염으로 입원")


def test_two_non_pregnancy_chapters_are_both_weak() -> None:
    index = _index()

    assert index.lookup("어깨 아탈구로 정형외과 통원") == ()
    assert set(index.lookup_weak("어깨 아탈구로 정형외과 통원")) == {"K07", "S03"}


def test_a_weak_tail_still_hides_the_shorter_key_inside_it() -> None:
    # 가리지 않으면 '심장질환' 안의 `장질환`(A07 장 질환)이 확실 코드로 튀어나왔다.
    index = _index()
    narrative = "협심증은 심장질환에 속한다"

    assert "A07" not in index.lookup(narrative)
    assert "A07" not in index.lookup_weak(narrative)
    assert set(index.lookup_weak(narrative)) == {"E05", "I27"}


@pytest.mark.parametrize(
    "narrative,exclusion,code",
    [
        ("요로감염으로 3일 입원, 청구 80만원", PREGNANCY_EXCLUSION, "O86"),
        ("가려움증으로 피부과 통원, 청구 5만원", MENTAL_EXCLUSION, "F45"),
    ],
)
def test_the_exclusion_is_reached_only_as_uncertain(narrative, exclusion, code) -> None:
    index = _index()
    claim = extract_claim(
        "C", BENEFIT, ENROLLED, narrative, enrich=index.lookup, weak=index.lookup_weak
    )

    # 확실 면책(부지급)은 아니고, 불확실 면책(사람 확인)으로는 간다.
    assert matches(exclusion, claim.diagnosis_codes) == ()
    assert matches(exclusion, claim.weak_codes) == (code,)


# --- 실제 상병마스터로 ---


@pytest.fixture(scope="module")
def master() -> KcdIndex:
    if not DEFAULT_CSV.exists():
        pytest.skip("상병마스터가 없다 — kcd.collect를 먼저 돌릴 것")
    return load_index()


# 2026-10-21 재현 문장과 main이 확실 코드로 낸 면책 코드.
REPRODUCED = (
    ("수술 전 마취제 투여 후 무릎 관절경 수술", ("O29", "O74")),
    ("요로감염으로 3일 입원", ("O08", "O86")),
    ("흡인폐렴으로 중환자실 입원", ("O29", "O89")),
    ("폐렴이 합병된 상태로 입원", ("O21",)),
    ("뇌출혈로 개두술 시행", ("O83",)),
    ("어깨 아탈구로 정형외과 통원", ("K07",)),
    ("가려움증으로 피부과 통원", ("F45",)),
    ("영양 불균형으로 수액 치료", ("O08", "O33")),
)


@pytest.mark.parametrize("narrative,codes", REPRODUCED)
def test_master_makes_no_certain_exclusion_code_from_a_tail(master, narrative, codes) -> None:
    certain = master.lookup(narrative)

    assert all(matches(text, certain) == () for text in EXCLUSIONS)
    # 버리지 않았다 — 불확실 면책으로 사람에게 간다.
    assert set(codes) <= set(master.lookup_weak(narrative))


@pytest.mark.parametrize(
    "narrative,code",
    [
        ("임신 중 심한 입덧으로 입원했습니다", "O21"),
        ("임신 32주 요로감염으로 입원", "O86"),
        ("태반조기박리로 응급 제왕절개", "O45"),
        ("우울증으로 정신과 통원치료를 받고 있습니다", "F33"),
        ("치주염으로 잇몸 치료", "K05"),
        ("요로감염으로 3일 입원", "N39"),
        ("흡인폐렴으로 중환자실 입원", "J69"),
    ],
)
def test_master_keeps_a_decided_chapter_certain(master, narrative, code) -> None:
    assert code in master.lookup(narrative)


@pytest.mark.parametrize(
    "narrative,code",
    [
        # 임신 고유 병명이지만 서술에 맥락 낱말이 없다.
        ("자간증으로 입원", "O15"),
        ("조기진통으로 입원", "O60"),
        # 낱말이 두 장에 걸친다 — 치은염은 A69(괴사궤양성)와 K05, 불면증은 F51(비기질성)과 G47.
        ("치은염으로 치과 치료", "K05"),
        ("불면증으로 수면클리닉", "F51"),
    ],
)
def test_master_sends_a_context_dependent_name_to_a_person(master, narrative, code) -> None:
    # 이번 규칙의 대가다 — 확실 면책이 아니라 불확실 면책(사람 확인)이 된다.
    assert code not in master.lookup(narrative)
    assert code in master.lookup_weak(narrative)


@pytest.mark.parametrize(
    "narrative,injury",
    [
        # 경막하출혈 = I62(비외상성)·P10(분만 손상)·S06(외상성), 아탈구 = K07·S03.
        ("낙상 후 경막하출혈로 수술", "S06"),
        ("교통사고로 치아 아탈구로 통원", "S03"),
    ],
)
def test_master_weak_injury_code_does_not_turn_the_claim_into_a_disease_claim(
    master, narrative, injury
) -> None:
    from clausegraph.agents.amount_rules import find_rule

    claim = extract_claim(
        "C", BENEFIT, ENROLLED, narrative, enrich=master.lookup, weak=master.lookup_weak
    )

    # 상해 코드가 약해지자 외인 코드(W19·V87)만 남아 질병급여 규칙을 골랐다(검토 HIGH-1).
    # 상해인지 질병인지 모르면 규칙을 고르지 않는다 — 금액을 계산하지 않고 사람에게 간다.
    assert injury in claim.weak_codes
    assert find_rule(BENEFIT, None, claim.diagnosis_codes) is None
