"""급여 통원의 확정 지급액이 열리며 드러난 과다지급 경로 셋 (notes/049).

PR #13이 영수증의 요양기관 종류ㆍ본인부담률을 받아 급여 통원의 확정 지급액을 처음
열었다. 그 전에는 MCP의 급여 통원이 늘 상한(HUMAN_REVIEW)이라 아래 세 경로가 가려져
있었다. 셋 다 main에 원래 있던 것이다(notes/045 끝 '남은 것').

1. 급여 실손에 "비급여"라고 적은 청구가 (2)질병급여로 확정된다.
2. 서류 코드를 주면 사전이 읽은 사유 코드(X80)가 약한 코드로 내려가는데, 사유 코드
   면책은 강한 코드만 본다 — 자해 서술 + 진단서 S82.0이 PARTIAL.
3. 후속 질문의 KCD 정규식이 옛 판본이라 "F20으로"ㆍ"F200"을 새 사실로 못 본다.
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.access import Principal
from clausegraph.agents import exclusion, guardrails
from clausegraph.agents.amount_rules import (
    BENEFIT,
    MILD,
    NONCOVERED,
    SEVERE,
    carved_out_items,
    find_rule,
    why_no_rule,
)
from clausegraph.agents.extract import codes_in, extract_claim
from clausegraph.agents.followup import FollowUp, answer, classify, detect_new_facts
from clausegraph.agents.models import Adjudication, ClaimHistory, Decision, Evidence
from clausegraph.agents.session import SessionStore

ENROLLED = date(2026, 7, 1)
VERSION = "20260506"
EVERYONE = Principal.everything()


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


def _fake_graph(monkeypatch, module, *, screen_too: bool = True) -> None:
    """DB를 부르는 곳만 바꾼다. 나머지(면책 거르기ㆍ금액ㆍ가드레일)는 실제 코드가 돈다."""
    monkeypatch.setattr(module, "resolve_version", lambda *a, **k: VERSION)
    monkeypatch.setattr(module, "find_coverage", _coverage)
    if screen_too:
        monkeypatch.setattr(module, "screen", lambda *a, **k: ([], 70))


def _both(monkeypatch, claim, *, screen_too: bool = True):
    """자체 구현과 LangGraph 구현의 판정. LangGraph가 없으면 자체 구현만."""
    from clausegraph.agents import orchestrator

    _fake_graph(monkeypatch, orchestrator, screen_too=screen_too)
    own = orchestrator.adjudicate(None, claim, principal=EVERYONE)
    try:
        from clausegraph.agents import graph_orchestrator
    except ImportError:
        return own, None
    _fake_graph(monkeypatch, graph_orchestrator, screen_too=screen_too)
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


# ============================================================================
# 1. 급여 실손의 "비급여" — 제4조의2 ① "1. 비급여 의료비"는 보상하지 않는다
# ============================================================================

NONCOVERED_OUTPATIENT = "2026-08-10 비급여 도수치료 통원, 진료비 10만원"
NONCOVERED_INPATIENT = "2026-08-10부터 2026-08-12까지 3일 입원, 비급여 도수치료, 진료비 100만원"


def _benefit(narrative: str, codes=("M54.5",), **extra):
    return extract_claim(
        "MCP", BENEFIT, ENROLLED, narrative,
        diagnosis_codes=codes, history=extra.pop("history", ClaimHistory()), **extra,
    )


def test_benefit_outpatient_naming_noncovered_is_not_paid(monkeypatch) -> None:
    """재현 입력 — 의원ㆍ본인부담률 0.3이면 PARTIAL 70,000원이 나갔다."""
    claim = _benefit(NONCOVERED_OUTPATIENT, institution="의원", copay_rate=0.3)
    own, graph = _both(monkeypatch, claim)

    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    assert guardrails.AMOUNT_NOT_COMPUTED in own.guardrails
    # 무엇을 왜 확인해야 하는지가 사유에 남는다.
    assert "'비급여'" in own.reason
    assert "제4조의2" in own.reason
    _same(own, graph)


def test_benefit_inpatient_naming_noncovered_is_not_paid(monkeypatch) -> None:
    """입원은 영수증 값이 없어도 계산된다 — main에서 PARTIAL 800,000원이었다."""
    own, graph = _both(monkeypatch, _benefit(NONCOVERED_INPATIENT))

    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    _same(own, graph)


def test_benefit_noncovered_through_mcp(monkeypatch) -> None:
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.agents import orchestrator
    from clausegraph.mcp_server import server

    _fake_graph(monkeypatch, orchestrator)
    monkeypatch.setattr(server, "_product_names", lambda: ((BENEFIT,), (BENEFIT,)))
    monkeypatch.setattr(server, "driver", lambda: None)
    monkeypatch.setattr(server, "lookup", lambda *a, **k: ())
    monkeypatch.setattr(server, "weak_lookup", lambda *a, **k: ())

    text = server.adjudicate_claim(
        product=BENEFIT, enrolled_on="2026-07-01", narrative=NONCOVERED_OUTPATIENT,
        diagnosis_codes="M54.5", institution="의원", copay_rate=0.3,
        paid_this_year=0, outpatient_visits_this_year=0, self_paid_this_year=0,
    )

    assert "판정 HUMAN_REVIEW   지급액 0원" in text


@pytest.mark.parametrize(
    "text",
    [
        "비급여 도수치료",
        "비급여 진료비 포함",
        "급여ㆍ비급여 합계 10만원",
        "비 급여 항목",
        "비급여분",
    ],
)
def test_benefit_rules_are_not_chosen_when_noncovered_is_named(text) -> None:
    assert carved_out_items(BENEFIT, text) == {NONCOVERED: "비급여"}
    assert find_rule(BENEFIT, None, ("M54.5",), treatment=text) is None
    # 불확실 면책이 (2)를 가리켜도 마찬가지다.
    assert find_rule(BENEFIT, "(2)질병급여", ("M54.5",), treatment=text) is None
    reason = why_no_rule(BENEFIT, ("M54.5",), abroad=None, treatment=text)
    assert "제4조의2" in reason
    assert reason.endswith("심사자 확인이 필요하다")


@pytest.mark.parametrize(
    "text",
    [
        "허리 MRI 촬영",  # 급여 MRI는 급여 의료비다(notes/048)
        "치료비 급여 항목 10만원",  # '치료비'의 비 — '비급여'가 아니다
        "진단비 보험금 청구",
        "실비보험으로 청구",
        "급여 진료 통원",
    ],
)
def test_benefit_lookalikes_keep_the_benefit_rule(text) -> None:
    assert carved_out_items(BENEFIT, text) == {}
    assert find_rule(BENEFIT, None, ("M54.5",), treatment=text).coverage == "(2)질병급여"


@pytest.mark.parametrize("product", [SEVERE, MILD])
def test_noncovered_products_are_unaffected_by_the_word(product) -> None:
    """비급여 특약에서 "비급여"는 보장 대상 그 자체다."""
    assert carved_out_items(product, "비급여 진료 통원") == {}
    assert find_rule(product, None, ("M54.5",), treatment="비급여 진료 통원") is not None


def test_benefit_follow_up_that_reveals_noncovered_is_a_new_fact() -> None:
    claim = _benefit("2026-08-10 허리 통증 통원", institution="의원", copay_rate=0.3)

    assert "비급여" in detect_new_facts("사실 비급여 도수치료였어요. 지급액 얼마예요?", claim)
    # 이미 아는 값을 되물은 것은 새 사실이 아니다.
    known = _benefit(NONCOVERED_OUTPATIENT, institution="의원", copay_rate=0.3)
    assert "비급여" not in detect_new_facts("비급여는 왜 안 되나요?", known)


def test_benefit_correction_keeps_the_noncovered_marker(monkeypatch) -> None:
    """notes/048의 표지 남기기 — "비급여"를 빼고 고쳐 말해도 사람에게 간다."""
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.mcp_server.server import _revise

    claim = _benefit(NONCOVERED_OUTPATIENT, institution="의원", copay_rate=0.3)
    revised = _revise(claim, -1, -1, -1, -1, "2026-08-10 도수치료 통원, 진료비 10만원으로 정정")
    own, graph = _both(monkeypatch, revised)

    assert revised.held_treatment == ("비급여",)
    assert own.decision is Decision.HUMAN_REVIEW
    _same(own, graph)


# ============================================================================
# 2. 약한 코드의 사유 코드 — 자해(X80)는 서류 코드가 있어도 사람에게
# ============================================================================

SELF_HARM_NARRATIVE = "높은 곳에서 뛰어내림에 의한 의도적 자해로 발목 골절, 의원 통원"
# 표준약관 실손 제4조의 실제 문장 꼴. 사유 면책에는 코드 범위가 없다.
SELF_HARM_CLAUSE = (
    "피보험자가 고의로 자신을 해친 경우. 다만, 피보험자가 심신상실 등으로 자유로운"
    " 의사결정을 할 수 없는 상태에서 자신을 해친 경우에는 보상합니다."
)


def _dictionary(_text: str) -> tuple[str, ...]:
    # 실제 사전(`terminology.lookup`)이 이 서술에서 읽는 값 — '자해'와 '발목 골절'.
    return ("X80", "S82")


def _self_harm(product: str, stated=("S82.0",), **receipt):
    return extract_claim(
        "R", product, ENROLLED, SELF_HARM_NARRATIVE,
        enrich=_dictionary, history=ClaimHistory(),
        diagnosis_codes=stated, claimed_amount=45_000, **receipt,
    )


@pytest.fixture
def self_harm_clause(monkeypatch):
    """면책 열거와 변별어 사전을 채워 실제 `screen`을 돌린다. 변별어는 비워 둔다 —
    표현 일치가 아니라 사유 코드로 걸리는지를 본다."""
    for product in (BENEFIT, SEVERE):
        monkeypatch.setitem(
            exclusion._EXCLUSION_CACHE,
            (product, VERSION),
            [{"node_uid": f"{VERSION}/{product}/4/1", "number": "4",
              "title": "보상하지 않는 사항", "text": SELF_HARM_CLAUSE,
              "coverage": None, "exceptions": []}],
        )
    monkeypatch.setitem(exclusion._TOKEN_CACHE, VERSION, frozenset())


def test_stated_codes_push_the_dictionary_reason_code_down_to_weak() -> None:
    claim = _self_harm(BENEFIT)

    assert claim.diagnosis_codes == ("S82.0",)
    assert "X80" in claim.weak_codes


@pytest.mark.usefixtures("self_harm_clause")
def test_a_weak_reason_code_is_an_uncertain_exclusion() -> None:
    hits, considered = exclusion.screen(None, _self_harm(BENEFIT), VERSION, principal=EVERYONE)

    assert considered == 1
    assert [(hit.certain, hit.matched_codes) for hit in hits] == [(False, ("X80",))]
    assert "확실하지 않게 읽힌 코드 X80(의도적 자해)" in hits[0].reason


@pytest.mark.usefixtures("self_harm_clause")
@pytest.mark.parametrize(
    ("product", "receipt"),
    [
        (BENEFIT, {"institution": "의원", "copay_rate": 0.3}),  # 고치기 전 PARTIAL 31,500원
        (SEVERE, {}),  # 고치기 전 PARTIAL 15,000원 — main에서도
    ],
)
def test_self_harm_with_a_stated_fracture_code_goes_to_a_person(
    monkeypatch, product, receipt
) -> None:
    own, graph = _both(monkeypatch, _self_harm(product, **receipt), screen_too=False)

    assert own.decision is Decision.HUMAN_REVIEW
    assert own.amount == 0
    assert guardrails.UNCERTAIN_EXCLUSION in own.guardrails
    _same(own, graph)


@pytest.mark.usefixtures("self_harm_clause")
def test_without_stated_codes_the_strong_reason_code_still_fires(monkeypatch) -> None:
    claim = _self_harm(BENEFIT, stated=(), institution="의원", copay_rate=0.3)
    hits, _ = exclusion.screen(None, claim, VERSION, principal=EVERYONE)

    assert claim.diagnosis_codes == ("X80", "S82")
    # 강한 코드의 문구는 그대로다 — 서류ㆍ서술에서 확실하게 읽힌 코드.
    assert hits[0].reason.startswith("진단코드 X80(의도적 자해)")


@pytest.mark.usefixtures("self_harm_clause")
def test_a_weak_code_that_is_not_a_reason_code_raises_nothing() -> None:
    claim = extract_claim(
        "R", BENEFIT, ENROLLED, "계단에서 넘어져 발목 골절, 의원 통원",
        enrich=lambda _text: ("W10", "S82"), history=ClaimHistory(),
        diagnosis_codes=("S82.0",), claimed_amount=45_000,
    )

    assert exclusion.screen(None, claim, VERSION, principal=EVERYONE)[0] == []


# ============================================================================
# 3. 후속 질문의 진단코드 — 사실추출과 같은 문법(`codes_in`)으로 읽는다
# ============================================================================


def _clinic_claim():
    return extract_claim(
        "C", BENEFIT, ENROLLED, "2026-08-10 급성 기관지염 통원",
        diagnosis_codes=("J20.9",), claimed_amount=45_000, history=ClaimHistory(),
        institution="의원", copay_rate=0.3,
    )


def _session(claim):
    result = Adjudication(
        claim_id=claim.claim_id, decision=Decision.PARTIAL, amount=31_500,
        reason="통원 공제 13,500원(여러 항 중 큰 쪽) / 연간한도 잔액 50,000,000원 이내",
        applied_version=VERSION,
    )
    return SessionStore().open(claim, result, "첫 판정")


@pytest.mark.parametrize(
    "question",
    [
        "사실 F20으로 진단받은 적이 있어요. 금액은 얼마죠?",
        "F200도 있어요. 금액은 얼마죠?",
        "f32를 같이 진단받았는데 금액은 얼마죠?",
        "기왕증F20 있는데 지급액은요?",
        "Ｆ２０ 진단도 받았어요",
    ],
)
def test_a_code_with_a_particle_or_without_a_dot_is_a_new_fact(question) -> None:
    claim = _clinic_claim()

    assert "진단코드" in detect_new_facts(question, claim)
    kind, text = answer(_session(claim), question)
    assert kind is FollowUp.NEW_FACTS
    # 낡은 확정액을 설명하지 않는다.
    assert "31,500원" not in text


@pytest.mark.parametrize(
    "question",
    ["J20.9로 진단받았는데 왜 이 금액이죠?", "J209 때문에 통원했는데 금액은요?", "j20.9가 맞나요?"],
)
def test_the_known_code_written_differently_is_not_new(question) -> None:
    assert "진단코드" not in detect_new_facts(question, _clinic_claim())


@pytest.mark.parametrize(
    "question",
    [
        "E-9 비자로 일하는데 왜 안 되죠?",
        "COVID19 검사비는요?",
        "2026년 8월 12일에 갔어요",
        "USD 1,800",
    ],
)
def test_other_tokens_are_not_read_as_codes(question) -> None:
    assert "진단코드" not in detect_new_facts(question, _clinic_claim())


def test_follow_up_reads_codes_with_the_extraction_grammar() -> None:
    """두 단계가 다른 문법으로 읽으면 한쪽이 놓친 코드가 낡은 판정을 설명하게 만든다."""
    claim = _clinic_claim()
    for question in ("F20으로", "F200도", "f32를", "S82.0 골절", "J20.9", "K02,F20"):
        fresh = set(codes_in(question)) - set(claim.diagnosis_codes)
        assert ("진단코드" in detect_new_facts(question, claim)) is bool(fresh), question


def test_classify_puts_the_new_code_before_the_amount_question() -> None:
    assert classify("F20으로 진단받았는데 금액은 얼마죠?", _clinic_claim()) is FollowUp.NEW_FACTS
