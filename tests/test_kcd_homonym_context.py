"""병명을 품은 병명 아닌 낱말 — 확실 코드가 아니라 약한 코드 (notes/046).

긴 열쇠는 부분 일치라 '세계자연유산'의 `자연유산`(O03)이 확실 코드가 됐고,
임신·출산(O00∼O99) 면책이 **확실 면책 -> 부지급**으로 걸렸다(2026-10-21 반박 검증).
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents.extract import extract_claim
from clausegraph.agents.kcd import matches
from clausegraph.kcd.index import DEFAULT_CSV, Disease, KcdIndex, load_index

OVERSEAS = "기본형 해외여행 실손의료보험"
ENROLLED = date(2026, 7, 1)
PREGNANCY_EXCLUSION = "임신, 출산(제왕절개를 포함합니다), 산후기(O00∼O99)"

_ROWS = (
    ("O03", "자연유산"),
    ("O03", "유산"),
    ("O02", "계류유산"),
    ("K56", "장유착"),
    ("K66", "복막유착"),
)


def _index() -> KcdIndex:
    return KcdIndex([Disease(code=c, name=n, english="", complete=True) for c, n in _ROWS])


@pytest.mark.parametrize(
    "narrative,code",
    [
        ("세계자연유산 관광 중 넘어져", "O03"),
        ("국가자연유산 탐방 중 미끄러짐", "O03"),
        ("유네스코 자연유산 트레킹", "O03"),
        # '지정'만 앞말 패턴에 걸린다 — 뒷말 패턴에 없는 '산책'으로 둔다.
        ("국가지정 자연유산 산책 중 넘어짐", "O03"),
        # 띄어 쓴 '자연 유산'은 `자연유산` 열쇠가 안 맞고 `유산`만 맞는다.
        ("세계 자연 유산 산책 중 넘어져", "O03"),
        ("자연유산 탐방로에서 발목을 접질림", "O03"),
        ("자연유산 여행 중 넘어짐", "O03"),
        ("제주 자연유산 방문 중 넘어짐", "O03"),
        ("세계 유산 탐방 중 넘어짐", "O03"),
        ("문화 유산 답사 중 낙상", "O03"),
        ("국가 유산청 행사 참석 중 넘어짐", "O03"),
        ("유산소 운동 중 무릎 통증", "O03"),
        ("유산소운동 후 근육통", "O03"),
        ("유산소를 하다 넘어짐", "O03"),
        ("유산균 복용 후 설사", "O03"),
        ("유산슬 먹고 식중독", "O03"),
        ("복강경 수술, 장유착방지제 도포", "K56"),
        ("복막유착 방지제 사용", "K66"),
        ("복막유착 예방제 사용", "K66"),
    ],
)
def test_disease_key_inside_a_non_disease_word_is_only_a_weak_code(narrative, code) -> None:
    index = _index()

    assert code not in index.lookup(narrative)
    assert code in index.lookup_weak(narrative)


@pytest.mark.parametrize(
    "narrative,code",
    [
        ("자연유산 후 소파술", "O03"),
        ("자연유산으로 입원", "O03"),
        ("유산 소파술 시행", "O03"),
        # '유산 소파술'을 붙여 쓴 것 — '유산소'와 글자가 같다.
        ("유산소파술 시행", "O03"),
        # '유산 소견'·'유산 소독'을 붙여 쓴 것 (검토 MEDIUM-1).
        ("유산소견으로 입원", "O03"),
        ("자연유산소견", "O03"),
        ("유산소독 후 퇴원", "O03"),
        ("자연 유산 후 소파술", "O03"),
        ("계류유산 진단", "O02"),
        ("복막유착 박리술", "K66"),
        # 앞의 것은 관광지지만 뒤의 것은 띄어 쓴 진짜 병명이다.
        ("세계자연유산 관광 중 자연유산", "O03"),
        ("자연유산 후 소파술, 이후 세계자연유산 관광", "O03"),
        # 관광지 범위가 끝난 바로 다음 글자부터는 가리지 않는다.
        ("세계자연유산자연유산", "O03"),
        # 약하게 맞은 긴 열쇠가 그 안의 짧은 열쇠의 진짜 출현을 가리지 않는다.
        ("세계자연유산 관광 중 유산", "O03"),
        ("유착방지제 도포 후에도 복막유착 재발", "K66"),
    ],
)
def test_real_disease_names_stay_certain(narrative, code) -> None:
    assert code in _index().lookup(narrative)


@pytest.mark.xfail(strict=True, reason="문맥 목록 밖의 동음이의 — notes/046 남은 것")
@pytest.mark.parametrize("narrative", ["부모님 유산 상속 문제로 불면", "유산염 수액 투여"])
def test_known_homonyms_outside_the_context_list(narrative) -> None:
    assert "O03" not in _index().lookup(narrative)


def test_a_heritage_site_reaches_the_pregnancy_exclusion_only_as_uncertain() -> None:
    index = _index()
    claim = extract_claim(
        "C", OVERSEAS, ENROLLED, "세계자연유산 관광 중 넘어져 손목을 다침",
        enrich=index.lookup, weak=index.lookup_weak,
    )

    # 확실 면책(부지급)은 아니고, 불확실 면책(사람 확인)으로는 간다.
    assert matches(PREGNANCY_EXCLUSION, claim.diagnosis_codes) == ()
    assert matches(PREGNANCY_EXCLUSION, claim.weak_codes) == ("O03",)


# --- 실제 상병마스터로 ---


@pytest.fixture(scope="module")
def master() -> KcdIndex:
    if not DEFAULT_CSV.exists():
        pytest.skip("상병마스터가 없다 — kcd.collect를 먼저 돌릴 것")
    return load_index()


@pytest.mark.parametrize(
    "narrative",
    [
        "세계자연유산 관광 중 넘어져",
        "국가자연유산 탐방 중 미끄러짐",
        "자연유산 여행 중 넘어짐",
        "세계 유산 탐방 중 넘어짐",
        "유산소 운동 중 무릎 통증",
        "유산균 복용 후 설사",
        "유산슬 먹고 식중독",
    ],
)
def test_master_does_not_make_o03_certain_from_a_non_disease_word(master, narrative) -> None:
    assert "O03" not in master.lookup(narrative)
    assert "O03" in master.lookup_weak(narrative)


@pytest.mark.parametrize(
    "narrative",
    [
        "자연유산 후 소파술",
        "유산소파술 시행",
        "유산소견으로 입원",
        "세계자연유산 관광 중 자연유산",
    ],
)
def test_master_still_finds_a_real_miscarriage(master, narrative) -> None:
    assert "O03" in master.lookup(narrative)
