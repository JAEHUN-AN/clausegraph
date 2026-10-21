"""비급여 특약에 "급여"라고 적은 청구 (notes/050).

특약1ㆍ2 제3조의 보상 대상은 "비급여의료비(…)"뿐이다. 급여 본인부담금은 기본형(급여
실손)이 보상한다. 그런데 "비급여 항목 없음, 급여 본인부담금만"이라고 적은 특약 청구가
(2)질병비급여로 계산됐다 — 종합병원ㆍ본인부담률 0.2, 입원 100만원에 특약1 700,000원,
특약2 500,000원(main f796cca, 실제 Neo4j). 통원 10만원이면 70,000원/50,000원.

notes/049가 급여 실손의 '비급여'에 쓴 장치(표지 → `find_rule` None → HUMAN_REVIEW, 재심사의
표지 남기기, 후속 질문의 새 사실)의 거울상이다.
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.access import Principal
from clausegraph.agents import guardrails
from clausegraph.agents.amount_rules import (
    BENEFIT,
    COVERED,
    MILD,
    MUSCULOSKELETAL,
    NONCOVERED,
    SEVERE,
    carved_out_items,
    find_rule,
    why_no_rule,
)
from clausegraph.agents.extract import extract_claim
from clausegraph.agents.followup import FollowUp, classify, detect_new_facts
from clausegraph.agents.models import ClaimHistory, Decision, Evidence

ENROLLED = date(2026, 7, 1)
VERSION = "20260506"
EVERYONE = Principal.everything()
RIDERS = (SEVERE, MILD)
PNEUMONIA = ("J18.9",)

INPATIENT = (
    "2026-08-10부터 2026-08-12까지 3일 입원, 폐렴, 비급여 항목 없음,"
    " 급여 본인부담금만 진료비 100만원"
)
OUTPATIENT = "2026-08-10 통원, 폐렴, 비급여 항목 없음, 급여 본인부담금만 진료비 10만원"
# 표지 하나씩 — 둘 중 하나만 적어도 사람에게 가야 한다.
ONLY_COPAY = "2026-08-10부터 2026-08-12까지 3일 입원, 폐렴, 급여 본인부담금만 진료비 100만원"
ONLY_NO_NONCOVERED = (
    "2026-08-10부터 2026-08-12까지 3일 입원, 폐렴, 비급여 항목 없음, 진료비 100만원"
)
PLAIN_INPATIENT = "2026-08-10부터 2026-08-12까지 3일 입원, 폐렴, 진료비 100만원"


def _coverage(driver, product, version, **kwargs):
    return [
        Evidence(
            node_uid=f"{version}|{product}|3",
            product=product,
            article_number="3",
            article_title="보장종목별 보상내용",
            quote="회사가 이 계약의 보험기간 중 보상하거나 공제하는 내용은 …",
            role="coverage",
        )
    ]


def _fake_graph(monkeypatch, module) -> None:
    """DB를 부르는 곳만 바꾼다. 나머지(면책 거르기ㆍ금액ㆍ가드레일)는 실제 코드가 돈다."""
    monkeypatch.setattr(module, "resolve_version", lambda *a, **k: VERSION)
    monkeypatch.setattr(module, "find_coverage", _coverage)
    monkeypatch.setattr(module, "screen", lambda *a, **k: ([], 70))


def _both(monkeypatch, claim):
    """자체 구현과 LangGraph 구현의 판정. LangGraph가 없으면 자체 구현만."""
    from clausegraph.agents import orchestrator

    _fake_graph(monkeypatch, orchestrator)
    own = orchestrator.adjudicate(None, claim, principal=EVERYONE)
    try:
        from clausegraph.agents import graph_orchestrator
    except ImportError:
        return own, None
    _fake_graph(monkeypatch, graph_orchestrator)
    graph_orchestrator._CACHE.clear()
    graph = graph_orchestrator.adjudicate(None, claim, principal=EVERYONE)
    return own, graph


def _same(own, graph) -> None:
    # compare.py의 전제 — 두 구현이 같은 답을 낸다.
    if graph is None:
        return
    assert (graph.decision, graph.reason, graph.amount, graph.guardrails) == (
        own.decision,
        own.reason,
        own.amount,
        own.guardrails,
    )


def _rider(product: str, narrative: str, **extra):
    """MCP처럼 누적을 0으로 준 청구 — 통원 횟수ㆍ연간한도가 확정이라 지급액이 확정으로 나간다."""
    return extract_claim(
        "MCP", product, ENROLLED, narrative,
        diagnosis_codes=extra.pop("codes", PNEUMONIA),
        history=ClaimHistory(paid_this_year=0, outpatient_visits_this_year=0),
        institution="종합병원", copay_rate=0.2, **extra,
    )


# ============================================================================
# 재현 — 특약1ㆍ2의 급여 청구가 (2)질병비급여로 확정됐다
# ============================================================================


@pytest.mark.parametrize("product", RIDERS)
@pytest.mark.parametrize("narrative", [INPATIENT, OUTPATIENT, ONLY_COPAY, ONLY_NO_NONCOVERED])
def test_rider_claim_stating_covered_care_is_not_paid(monkeypatch, product, narrative) -> None:
    own, graph = _both(monkeypatch, _rider(product, narrative))

    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    assert guardrails.AMOUNT_NOT_COMPUTED in own.guardrails
    # 무엇을 왜 확인해야 하는지가 사유에 남는다.
    assert "제3조" in own.reason
    assert "기본형" in own.reason
    _same(own, graph)


@pytest.mark.parametrize("product", RIDERS)
def test_plain_rider_claim_is_still_paid(monkeypatch, product) -> None:
    """대조 — 같은 청구에서 '급여' 표지만 빼면 전처럼 계산된다."""
    own, graph = _both(monkeypatch, _rider(product, PLAIN_INPATIENT))

    assert own.decision is Decision.PARTIAL
    assert own.amount == {SEVERE: 700_000, MILD: 500_000}[product]
    _same(own, graph)


@pytest.mark.parametrize("product", RIDERS)
def test_rider_covered_care_through_mcp(monkeypatch, product) -> None:
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.agents import orchestrator
    from clausegraph.mcp_server import server

    _fake_graph(monkeypatch, orchestrator)
    monkeypatch.setattr(server, "_product_names", lambda: ((product,), (product,)))
    monkeypatch.setattr(server, "driver", lambda: None)
    monkeypatch.setattr(server, "lookup", lambda *a, **k: ())
    monkeypatch.setattr(server, "weak_lookup", lambda *a, **k: ())

    text = server.adjudicate_claim(
        product=product, enrolled_on="2026-07-01", narrative=INPATIENT,
        diagnosis_codes="J18.9", institution="종합병원", copay_rate=0.2,
        paid_this_year=0, outpatient_visits_this_year=0, self_paid_this_year=0,
    )

    assert "판정 HUMAN_REVIEW   지급액 0원" in text


# ============================================================================
# 표지 — 무엇을 '급여'로 읽는가
# ============================================================================


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("급여 본인부담금만 진료비 100만원", "급여 본인부담금"),
        ("급여본인부담금 10만원", "급여본인부담금"),
        ("급여 본인 부담금 10만원", "급여 본인 부담금"),
        ("요양급여 본인부담금 10만원", "요양급여 본인부담금"),
        ("요양급여 중 본인부담금 10만원", "요양급여 중 본인부담금"),
        ("의료급여 본인부담금 10만원", "의료급여 본인부담금"),
        ("공단부담금 제외 10만원", "공단부담금"),
        ("급여 항목만 청구", "급여 항목"),
        ("급여항목 진료비", "급여항목"),
        ("급여분 10만원", "급여분"),
        ("급여 부분만 청구", "급여 부분"),
        ("급여의료비 청구", "급여의료비"),
        ("급여 진료비 10만원", "급여 진료비"),
        ("급여 진료 통원", "급여 진료"),
        # 섞인 청구 — 급여 몫을 특약이 보상하지 않는다.
        ("급여ㆍ비급여 합계 100만원", "급여ㆍ비급여"),  # NFKC가 바꾼 U+119E를 되돌려 적는다
        ("급여 및 비급여 합계 100만원", "급여 및 비급여"),
        ("비급여와 급여 합계 100만원", "비급여와 급여"),
        # 비급여가 없다는 말 — 남는 것은 급여뿐이다. 부정문을 읽지 않는 049와 같은 쪽(사람)이다.
        ("비급여 항목 없음", "비급여 항목 없음"),
        ("비급여는 없고 진료비 10만원", "비급여는 없고"),
        ("비급여 아님", "비급여 아님"),
        ("비급여가 아닌 진료", "비급여가 아닌"),
        ("비급여 0원", "비급여 0원"),
        ("비급여 해당 없음", "비급여 해당 없음"),
        ("비 급여 항목 없음", "비 급여 항목 없음"),
        ("비보험 없음", "비보험 없음"),
        # 서식 문자 — 049의 반박 검토에서 '비급여'가 이 길로 빠져나갔다.
        ("급\u200b여 본인부담금", "급여 본인부담금"),
        ("비\u200b급여 없음", "비급여 없음"),
    ],
)
@pytest.mark.parametrize("product", RIDERS)
def test_covered_spellings_are_read_on_riders(product, text, word) -> None:
    assert carved_out_items(product, text) == {COVERED: word}
    assert find_rule(product, None, PNEUMONIA, treatment=text) is None
    # 불확실 면책이 (2)를 가리켜도 마찬가지다.
    assert find_rule(product, "(2)질병비급여", PNEUMONIA, treatment=text) is None


@pytest.mark.parametrize(
    "text",
    [
        # 반박 검토 — 자연스러운 표기가 짝을 빠져나가 700,000원/500,000원이 나갔다(main과 같다).
        "급여만 청구",
        "급여 일부본인부담금 10만원",  # 진료비 영수증의 칸 이름
        "본인일부부담금 10만원",
        "급여, 본인부담금만",
        "급여/본인부담금만",
        "급여의 본인부담금",
        "급여 환자부담금",
        "건강보험 본인부담금 10만원",
        "요양급여 항목만",
        "요양급여 진료비 10만원",
        "보험급여분",
        "보험자부담금 제외",
        "비급여: 없음",
        "비급여(없음)",
        "비급여 항목: 0원",
        "비급여 합계 0원",
        "비급여액 0원",
        "비급여 0",
        "비급여 제외",
        "비급여 미포함",
        "비급여 미발생",
        "비급여 전무",
        "비급여 일체 없음",
        "비급여가 아닙니다",
        "비급여 해당無",
        "비급여 급여 합계 100만원",
        "급여-비급여 합계",
        "급여、비급여 합계",
        "급여•비급여 합계",
        "급여 그리고 비급여",
        # 코드 검토 — 조사ㆍ띄어쓰기ㆍ구분자ㆍ어미가 끼면 빠져나갔다.
        "급여에 대한 본인부담금",
        "급 여 본인부담금",
        "비급여 비용 없음",
        "비급여 청구 없음",
        "비급여 : 0원",
        "비급여/없음",
        "비급여 아녜요",
        # 후속 질문 탐침 — 급여라고 밝히는 흔한 말.
        "사실 급여였어요",
        "급여입니다",
        "공단이 부담했어요",
        "공단에서 80% 냈어요",
        "건강보험 적용된 진료",
        "건강보험이 적용되는 검사",
        "급여였는지 비급여였는지 모름",  # 모른다는 말도 사람에게
        # 반박 검토 변형 328가지에서 남은 흔한 꼴.
        "급여 검사",
        "급여 수술 진료비",
        "급여 입원",
        "급여 처방",
        "급여 약값",
        "급여 비용",
        "급여비 10만원",
        "급여 청구",
        "급여로 처리",
        "요양급여비용 총액",
        "일부본인부담금",
        "급여에서 본인부담",
        "급여 내 본인부담금",
        "급여 적용 진료",
        "급여 대상 진료",
        "건강보험 적용 진료비",
        "국민건강보험 적용 진료",
        "모두 급여",
        "비급여—없음",
        "비급여 무",
        "비급여 미해당",
        "비급여는 해당이 없음",
        "비급여 해당 안 됨",
        "비급여 불포함",
        "비급여 빼고",
        "비급여 총액 0원",
        "비급여 진료를 받지 않음",
        "비급여 진료 안 받음",
        "비급여 X",
        "비급여 ₩0",
        "급여 또는 비급여",
        "급여하고 비급여",
        "비급여 외 급여",
        "급여;비급여",
        "공단청구액",
        "공단 몫",
        # 반박 검토 HIGH — 짝 사이에 금액이 끼면 놓쳐, 청구액을 급여 80만원으로 읽어 지급했다.
        "3일 입원, 폐렴, 급여 80만원, 비급여 20만원, 합계 진료비 100만원",
        "3일 입원, 폐렴, 진료비 100만원 중 비급여 30만원",
        "3일 입원, 폐렴, 진료비 100만원(비급여 30만원 포함)",
        "3일 입원, 폐렴, 진료비 100만원, 그중 비급여 30만원",
        # 결합 문자ㆍ변형 선택자ㆍ한글 채움 문자 — 서식 문자(Cf)만 지우면 표지가 끊겼다.
        "급\u034f여 본인부담금만",
        "급\ufe0f여 본인부담금만",
        "급\u3164여 본인부담금만",
        "급\u0301여 본인부담금만",
    ],
)
@pytest.mark.parametrize("product", RIDERS)
def test_more_covered_spellings_are_read_on_riders(product, text) -> None:
    assert COVERED in carved_out_items(product, text)
    assert find_rule(product, None, PNEUMONIA, treatment=text) is None


@pytest.mark.parametrize(
    ("text", "word"),
    [
        # 부정어는 어미까지 — 조사로 보고 떼면 '비급여 없'이 된다.
        ("비급여 없이 진료", "비급여 없이"),
        ("비급여 없는 진료", "비급여 없는"),
        ("급여 80만원, 비급여 20만원", "급여 80만원"),
        ("진료비 100만원(비급여 30만원 포함)", "비급여 30만원 포함"),
        ("진료비 100만원, 비급여 30만원", "비급여 30만원"),
    ],
)
def test_the_word_in_the_reason_is_whole(text, word) -> None:
    assert carved_out_items(SEVERE, text) == {COVERED: word}


def test_a_shaded_character_in_the_input_does_not_block_the_marker() -> None:
    """검토 LOW — 가림 글자를 입력의 '▒'로 두었을 때는 그 글자를 앞에 붙여 표지를 막을 수 있었다.
    가림 글자는 사설 영역이고 입력의 사설 영역 글자는 먼저 지운다."""
    assert COVERED in carved_out_items(SEVERE, "▒급여 본인부담금만")
    assert COVERED in carved_out_items(SEVERE, chr(0xE000) + "급여 본인부담금만")


@pytest.mark.parametrize(
    "head",
    [
        "급여", "비급여", "급여 본인", "공단", "공단이", "100만원", "비급여 30만원", "비급여 항목",
        "비급여를", "급여 적용", "건강보험", "건강보험이",
    ],
)
def test_long_blank_runs_do_not_stall_the_marker(head) -> None:
    """검토 HIGH — 반복을 맞붙인 첫 판은 '급여' 뒤 공백 10만 칸에 78초가 걸렸다."""
    import time

    started = time.perf_counter()
    carved_out_items(SEVERE, head + " " * 100_000 + "x")
    assert time.perf_counter() - started < 1.0


def test_the_word_kept_for_review_has_no_line_break() -> None:
    assert carved_out_items(SEVERE, "급여\n본인부담금만") == {COVERED: "급여 본인부담금"}


def test_invisible_marks_do_not_hide_the_earlier_markers() -> None:
    """같은 정리를 048ㆍ049 표지도 거친다."""
    assert find_rule(SEVERE, None, ("M54.5",), treatment="도\u034f수치료 통원") is None
    assert find_rule(SEVERE, None, ("M54.5",), treatment="M\u034fRI 촬영") is None
    assert carved_out_items(BENEFIT, "비\u034f급여 도수치료 통원") == {NONCOVERED: "비급여"}


def test_the_reason_names_the_article_and_where_it_is_paid() -> None:
    text = "급여 본인부담금만 진료비 10만원"
    reason = why_no_rule(SEVERE, PNEUMONIA, abroad=None, treatment=text)

    assert reason.startswith("'급여 본인부담금' — 제3조")
    assert "기본형" in reason
    assert reason.endswith("심사자 확인이 필요하다")


@pytest.mark.parametrize(
    "text",
    [
        # 160건에서 '급여'가 나온 자리는 대부분 이 꼴이었다(notes/050).
        "국민건강보험법상 요양급여에 해당하지 않는 비급여 진료",
        "요양급여 대상 아님",
        "의료급여 수급자로 비급여 진료",
        "의료급여수급권자",
        "보험금 지급 여부",
        "보험금 지급여부를 결정",
        "휴업급여금 수령",
        "장해급여 청구",
        "월 급여에서 공제",
        "급여 실손과 함께 가입",
        "비급여와 급여 실손 모두 가입",
        # 비급여를 풀어 쓴 말 — '급여'가 들어도 비급여다.
        "건강보험 급여 적용 안 되는 진료",
        "급여 외 진료",
        "급여가 안 되는 진료",
        # 비급여 특약의 보장 대상 그 자체.
        "비급여 진료 통원",
        "비급여 항목 포함 진료비",
        "비급여 병실료",
        "비급여가 대부분",
        # '비급여 없음'은 홀로 선 '비급여'만 — 임의비급여ㆍ3대비급여가 없다는 말은
        # 급여만이라는 뜻이 아니다.
        "임의비급여 없음",
        "3대비급여는 제외",
        # '본인부담'만으로는 가르지 않는다 — 본인부담상한제ㆍ전액본인부담ㆍ산재 본인부담의료비.
        "본인부담금 10만원",
        "전액본인부담",
        "산재보험 요양급여 산정기준에 따라 발생한 실제 본인부담의료비",
        # 금액 — '의료급여 1종'의 숫자는 금액이 아니고, 금액이 하나뿐이면 섞였다고 볼 수 없다.
        "의료급여 1종 수급자",
        "비급여 진료비 10만원 통원",
        "비급여 10만원, 진료비 10만원",
        # '중'은 금액 뒤에서만 — "입원 중"은 기간이다.
        "입원 중 비급여 검사",
        # 비급여를 풀어 쓴 말.
        "급여 대상이 아닌 검사",
        "건강보험 적용 안 되는 검사",
        "비급여, 급여 외 진료",
        "비급여 및 급여 외 항목",
        "비급여, 급여 적용 안 되는 항목",
        # 공단ㆍ보험자 — 국민건강보험공단만. 산재(근로복지공단)ㆍ피보험자는 아니다.
        "근로복지공단 부담금",
        "피보험자 부담으로 낸 비급여 진료",
        # 본인부담률은 모든 영수증에 있는 값이다(구조화 입력 `copay_rate`) — 급여 청구라는
        # 말이 아니다.
        "건강보험 본인부담률 20%",
        "공단부담률 80%",
        "급여 본인부담률 20%",
        "본인일부부담율 30%",
        # 건강보험이 적용되지 않는다는 말은 비급여다. 실손보험 적용은 건강보험이 아니다.
        "건강보험 적용되지 않는 검사",
        "건강보험 적용 안 된 진료",
        "실손보험 적용되는지 문의",
        # 받는 쪽만 읽는다 — 적용ㆍ대상ㆍ해당의 부정은 비급여다. 요양급여 대상은 처리결과 문장에
        # 흔하다("국민건강보험 요양급여 대상 수술방법 또는 치료재료가 아닌 경우").
        "급여 적용 안 되는 검사",
        "급여 대상이 아닌 진료",
        "급여 해당 안 되는 검사",
        "건강보험 적용 대상이 아닌 수술",
        "요양급여 대상 수술방법이 아닌 경우",
        "요양급여 적용 여부",
        # '비급여 무'의 무는 홀로 — 무릎은 아니다. X선은 촬영이다.
        "비급여 무릎 검사",
        "비급여 X선 촬영",
    ],
)
@pytest.mark.parametrize("product", RIDERS)
def test_rider_lookalikes_keep_the_rider_rule(product, text) -> None:
    assert COVERED not in carved_out_items(product, text)
    assert find_rule(product, None, PNEUMONIA, treatment=text).coverage == "(2)질병비급여"


def test_benefit_is_unaffected_by_the_covered_marker() -> None:
    """급여 실손에서 '급여'는 보장 대상 그 자체다. '비급여 없음'은 049대로 '비급여'로 읽는다."""
    assert carved_out_items(BENEFIT, "급여 본인부담금만 진료비 10만원") == {}
    rule = find_rule(BENEFIT, None, PNEUMONIA, treatment="급여 본인부담금만")
    assert rule.coverage == "(2)질병급여"
    assert carved_out_items(BENEFIT, "비급여 항목 없음") == {NONCOVERED: "비급여"}


def test_covered_marker_sits_beside_the_item_markers() -> None:
    """특약1 '급여 본인부담금 + 도수치료' — 두 표지가 다 사유에 남는다."""
    text = "급여 본인부담금과 도수치료 진료비 10만원"

    assert set(carved_out_items(SEVERE, text)) == {COVERED, MUSCULOSKELETAL}
    reason = why_no_rule(SEVERE, ("M54.5",), abroad=None, treatment=text)
    assert "'급여 본인부담금'" in reason and "'도수치료'" in reason


# ============================================================================
# 재심사와 후속 질문 — 같은 표를 본다
# ============================================================================


@pytest.mark.parametrize("product", RIDERS)
def test_rider_follow_up_that_reveals_covered_care_is_a_new_fact(product) -> None:
    claim = _rider(product, PLAIN_INPATIENT)
    question = "사실 급여 본인부담금만 낸 거예요. 지급액 얼마예요?"

    # '시술'이 같이 붙으면 안 된다 — 급여는 의료행위가 아니라 구분이다.
    assert detect_new_facts(question, claim) == ("급여",)
    assert classify(question, claim) is FollowUp.NEW_FACTS
    # 이미 아는 값을 되물은 것은 새 사실이 아니다.
    known = _rider(product, INPATIENT)
    assert "급여" not in detect_new_facts("급여 본인부담금은 왜 안 되나요?", known)


@pytest.mark.parametrize("product", RIDERS)
def test_rider_correction_keeps_the_covered_marker(monkeypatch, product) -> None:
    """notes/048의 표지 남기기 — '급여'를 빼고 고쳐 말해도 사람에게 간다."""
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.mcp_server.server import _revise

    claim = _rider(product, ONLY_COPAY)
    revised = _revise(claim, -1, -1, -1, -1, PLAIN_INPATIENT + "으로 정정")
    own, graph = _both(monkeypatch, revised)

    assert revised.held_treatment == ("급여 본인부담금",)
    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    _same(own, graph)


@pytest.mark.parametrize("tail", [" 비", " 非", " (비", ", 비-", " 비ㆍ", " [비]", "\n비"])
@pytest.mark.parametrize("product", RIDERS)
def test_a_trailing_bi_does_not_mask_the_kept_marker(monkeypatch, product, tail) -> None:
    """반박 검토 — 남긴 낱말은 서술 뒤에 이어 붙는다. 새 서술 끝의 홀로 선 '비'가 그 경계를
    넘어 '비 + 급여 본인부담금'을 '비급여'로 가려, 표지를 지켰다고 말하면서 700,000원/500,000원이
    나갔다."""
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.mcp_server.server import _revise

    claim = _rider(product, ONLY_COPAY)
    revised = _revise(claim, -1, -1, -1, -1, PLAIN_INPATIENT + tail)
    own, graph = _both(monkeypatch, revised)

    assert revised.held_treatment == ("급여 본인부담금",)
    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    _same(own, graph)
