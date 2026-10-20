"""짧은 열쇠 앞에 붙은 말 — 병명 수식어면 병명, 아니면 다른 낱말 (검토 MEDIUM-1).

앞 글자가 한글이면 전부 버리던 때, 급여 실손의 '수술후섬망' 입원이 DENIED에서
PARTIAL 2,400,000원이 됐다. 수식어로 끝나면 병명으로 친다.
"""

from __future__ import annotations

import pytest

from clausegraph.kcd.index import Disease, KcdIndex


def _index(*rows: tuple[str, str]) -> KcdIndex:
    return KcdIndex([Disease(code=code, name=name, english="", complete=True)
                     for code, name in rows])


@pytest.mark.parametrize(
    "rows,narrative,expected",
    [
        ((("F05", "섬망"),), "수술후섬망으로 입원", ("F05",)),
        ((("F05", "섬망"),), "알코올성섬망", ("F05",)),
        ((("O03", "유산"),), "습관성유산", ("O03",)),
        # '자연'은 수식어 목록에서 뺐다('자연가스'). 상병마스터에 '자연유산'이 통째로 있다.
        ((("O03", "유산"), ("O03", "자연유산")), "자연유산 후 소파술", ("O03",)),
        ((("F05", "섬망"),), "야간섬망", ("F05",)),
        ((("F05", "섬망"),), "금단섬망", ("F05",)),
        ((("K64", "치질"),), "만성치질 수술", ("K64",)),
        ((("E66", "비만"),), "복부비만 진단", ("E66",)),
        ((("E66", "비만"),), "고도비만으로 위 절제", ("E66",)),
        ((("J18", "폐렴"),), "급성폐렴", ("J18",)),
    ],
)
def test_short_key_after_a_disease_modifier_still_matches(rows, narrative, expected) -> None:
    assert _index(*rows).lookup(narrative) == expected


@pytest.mark.parametrize(
    "narrative", ["검사비만 청구합니다", "치료비만 청구", "준비만 했다", "소비만 늘었다"]
)
def test_short_key_glued_to_a_non_modifier_still_does_not_match(narrative) -> None:
    assert _index(("E66", "비만")).lookup(narrative) == ()


def test_a_free_occurrence_after_a_glued_one_still_counts() -> None:
    # 첫 출현은 '검사비만'에 붙었지만 두 번째는 띄어 쓴 진짜 병명이다.
    assert _index(("E66", "비만")).lookup("검사비만 청구, 비만 치료") == ("E66",)


@pytest.mark.parametrize("narrative", ["비만", "진단: 비만", "BMI 35 비만", "BMI비만"])
def test_short_key_at_the_edges_or_after_latin_matches(narrative) -> None:
    assert _index(("E66", "비만")).lookup(narrative) == ("E66",)


def test_intravenous_injection_is_not_rosacea() -> None:
    assert _index(("L71", "주사")).lookup("정맥주사를 맞았다") == ()
