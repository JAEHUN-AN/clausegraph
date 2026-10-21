"""3대비급여ㆍ비급여 MRI 청구에 (1)(2) 규칙을 고르지 않는다 (notes/048).

2026-10-21 반박 검증에서 재현했다. `find_rule`이 진단코드로 상해/질병만 갈라
특약1 도수치료 청구를 (2)질병비급여(연 5천만원ㆍ통원 1회 20만원)로 계산했다 —
항목 한도 350만원을 이미 다 쓴 계약에 7만원을 지급했다. 약관은 (1)(2)에서 그
항목을 뺀다.

    특약1 제3조 (1)(2) ① "비급여의료비(3대비급여는 제외합니다)"
    특약2 제3조 (1)(2) ① "비급여의료비(비급여 자기공명영상진단은 제외합니다)"
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.access import Principal
from clausegraph.agents import guardrails
from clausegraph.agents.amount_rules import (
    _BY_KEY,
    _ITEM_RULES,
    INJECTION,
    MRI,
    MUSCULOSKELETAL,
    carved_out_items,
    find_rule,
    noncovered_items,
    why_no_rule,
)
from clausegraph.agents.extract import extract_claim
from clausegraph.agents.followup import detect_new_facts
from clausegraph.agents.models import Claim, ClaimHistory, Decision, Evidence

SEVERE = "실손의료보험 특별약관1(중증 비급여 실손의료비)"
MILD = "실손의료보험 특별약관2(비중증 비급여 실손의료비)"
BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
ENROLLED = date(2026, 7, 1)

# 반박 검증의 재현 입력 그대로 — MCP `adjudicate_claim`이 만드는 청구와 같다.
REPRO_NARRATIVE = "2026-08-10 도수치료 통원, 진료비 10만원"
REPRO_HISTORY = ClaimHistory(
    paid_this_year=3_500_000, outpatient_visits_this_year=10, self_paid_this_year=0
)


def _claim(product: str, narrative: str, codes: tuple[str, ...] = ("M54.5",), **extra):
    return extract_claim(
        "MCP",
        product,
        ENROLLED,
        narrative,
        history=extra.pop("history", REPRO_HISTORY),
        diagnosis_codes=codes,
        **extra,
    )


def _fake_graph(monkeypatch, module) -> None:
    """DB를 부르는 세 곳만 바꾼다. 나머지(금액ㆍ가드레일)는 실제 코드가 돈다."""

    def coverage(driver, product, version, **kwargs):
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

    monkeypatch.setattr(module, "resolve_version", lambda *a, **k: "20260506")
    monkeypatch.setattr(module, "find_coverage", coverage)
    monkeypatch.setattr(module, "screen", lambda *a, **k: ([], 70))


def _adjudicate(monkeypatch, claim):
    from clausegraph.agents import orchestrator

    _fake_graph(monkeypatch, orchestrator)
    return orchestrator.adjudicate(None, claim, principal=Principal.everything())


# --- 재현 입력이 PARTIAL로 나오지 않는다 (두 오케스트레이터 모두) ---


def test_repro_claim_is_not_partial_in_the_self_orchestrator(monkeypatch) -> None:
    result = _adjudicate(monkeypatch, _claim(SEVERE, REPRO_NARRATIVE))

    assert result.decision is not Decision.PARTIAL
    assert result.decision is Decision.HUMAN_REVIEW
    assert result.amount == 0
    assert guardrails.AMOUNT_NOT_COMPUTED in result.guardrails
    # 사람이 무엇을 확인해야 하는지가 사유에 남는다.
    assert "'도수치료'" in result.reason
    assert "3대비급여" in result.reason
    assert "3,500,000원ㆍ50회" in result.reason


def test_repro_claim_is_not_partial_in_the_langgraph_orchestrator(monkeypatch) -> None:
    pytest.importorskip("langgraph", reason="langgraph extra가 없으면 건너뛴다")
    from clausegraph.agents import graph_orchestrator, orchestrator

    _fake_graph(monkeypatch, graph_orchestrator)
    _fake_graph(monkeypatch, orchestrator)
    graph_orchestrator._CACHE.clear()

    claim = _claim(SEVERE, REPRO_NARRATIVE)
    result = graph_orchestrator.adjudicate(None, claim, principal=Principal.everything())
    own = orchestrator.adjudicate(None, claim, principal=Principal.everything())

    assert result.decision is Decision.HUMAN_REVIEW
    assert result.amount == 0
    # compare.py의 전제 — 두 구현이 같은 답을 낸다.
    assert (result.decision, result.reason, result.amount, result.guardrails) == (
        own.decision,
        own.reason,
        own.amount,
        own.guardrails,
    )


def test_mild_musculoskeletal_claim_is_held_end_to_end(monkeypatch) -> None:
    """특약2 도수치료 — 면책 검증이 놓쳐 5만원이 나가던 청구(notes/048)."""
    result = _adjudicate(monkeypatch, _claim(MILD, REPRO_NARRATIVE, history=ClaimHistory()))

    assert result.decision is Decision.HUMAN_REVIEW
    assert "제4조" in result.reason


def test_a_marker_only_in_the_procedure_is_seen(monkeypatch) -> None:
    claim = Claim(
        claim_id="C",
        product=SEVERE,
        enrolled_on=ENROLLED,
        incident_on=date(2026, 8, 10),
        diagnosis_codes=("M54.5",),
        procedure="도수치료",
        claimed_amount=100_000,
        history=REPRO_HISTORY,
        narrative="허리 통증 통원",
    )

    assert _adjudicate(monkeypatch, claim).decision is Decision.HUMAN_REVIEW


def test_a_claim_without_markers_is_still_computed(monkeypatch) -> None:
    claim = _claim(SEVERE, "2026-08-10 허리 통증 통원, 진료비 10만원", history=ClaimHistory())
    result = _adjudicate(monkeypatch, claim)

    assert result.decision is Decision.PARTIAL
    assert result.amount == 70_000


# --- 표지가 없으면 그대로 (1)(2) ---


def test_without_markers_the_disease_rule_is_still_chosen() -> None:
    rule = find_rule(SEVERE, None, ("M54.5",), treatment="허리 통증 통원, 진료비 10만원")

    assert rule is not None and rule.coverage == "(2)질병비급여"
    assert why_no_rule(SEVERE, ("M54.5",), abroad=None, treatment="허리 통증 통원") is None


def test_treatment_defaults_to_empty_so_old_callers_keep_their_rule() -> None:
    assert find_rule(SEVERE, None, ("S82.0",)).coverage == "(1)상해비급여"


# --- 특약1: 세 항목 모두 (3)3대비급여 ---


@pytest.mark.parametrize(
    ("text", "item", "limit"),
    [
        ("도수치료 통원", MUSCULOSKELETAL, "3,500,000원"),
        ("도수 치료 5회", MUSCULOSKELETAL, "3,500,000원"),
        ("도수 10회 받음", MUSCULOSKELETAL, "3,500,000원"),
        ("도수재활치료", MUSCULOSKELETAL, "3,500,000원"),
        ("어깨 체외충격파치료", MUSCULOSKELETAL, "3,500,000원"),
        ("어깨 충격파 치료", MUSCULOSKELETAL, "3,500,000원"),
        ("ESWT 시행", MUSCULOSKELETAL, "3,500,000원"),
        ("IMS 치료", MUSCULOSKELETAL, "3,500,000원"),
        ("무릎 증식치료", MUSCULOSKELETAL, "3,500,000원"),
        ("인대강화주사", MUSCULOSKELETAL, "3,500,000원"),
        ("비급여 주사 통원", INJECTION, "2,500,000원"),
        ("영양주사 맞음", INJECTION, "2,500,000원"),
        ("수액 처방", INJECTION, "2,500,000원"),
        ("닝겔 맞음", INJECTION, "2,500,000원"),
        ("링게르 맞음", INJECTION, "2,500,000원"),
        ("허리 MRI 촬영", MRI, "3,000,000원"),
        ("뇌 mra", MRI, "3,000,000원"),
        ("무릎 ＭＲＩ", MRI, "3,000,000원"),
        ("MRCP 촬영", MRI, "3,000,000원"),
        ("엠알아이 촬영", MRI, "3,000,000원"),
        ("자기공명영상진단", MRI, "3,000,000원"),
    ],
)
def test_severe_markers_route_to_the_item_not_the_disease_rule(text, item, limit) -> None:
    assert item in noncovered_items(text)
    assert find_rule(SEVERE, None, ("M54.5",), treatment=text) is None

    reason = why_no_rule(SEVERE, ("M54.5",), abroad=None, treatment=text)
    assert item in reason
    assert limit in reason
    assert reason.endswith("심사자 확인이 필요하다")


def test_inpatient_severe_claim_with_mri_is_also_held() -> None:
    # 3대비급여 공제는 "1회당"이라 입원에도 붙는다 — 입원 청구도 (1)(2)로 계산하지 않는다.
    text = "뇌출혈로 10일 입원, MRI 포함 진료비 800만원"

    assert find_rule(SEVERE, None, ("I61.9",), treatment=text) is None


def test_an_explicit_coverage_does_not_bypass_the_markers() -> None:
    """불확실 면책이 (2)를 가리켜도, 항목 규칙을 지목해도 표지가 있으면 고르지 않는다."""
    item_rule = "(3)3대비급여-근골격계이학요법치료ㆍ체외충격파치료"

    assert find_rule(SEVERE, "(2)질병비급여", ("M54.5",), treatment="도수치료") is None
    assert find_rule(SEVERE, item_rule, ("M54.5",), treatment="도수치료") is None
    # 표지가 없으면 notes/019대로 지목한 항목 규칙이 나온다.
    assert find_rule(SEVERE, item_rule, ("M54.5",)).coverage == item_rule


# --- 특약2: MRI는 (3), 이학요법ㆍ주사는 제4조 면책 ---


def test_mild_mri_routes_to_its_own_item() -> None:
    reason = why_no_rule(MILD, ("M54.5",), abroad=None, treatment="허리 자기공명영상 통원")

    assert find_rule(MILD, None, ("M54.5",), treatment="허리 자기공명영상 통원") is None
    assert "2,000,000원" in reason


@pytest.mark.parametrize("text", ["도수치료 통원", "체외충격파 치료", "통증 주사", "닝겔"])
def test_mild_musculoskeletal_and_injection_are_held_as_exclusions(text) -> None:
    reason = why_no_rule(MILD, ("M54.5",), abroad=None, treatment=text)

    assert find_rule(MILD, None, ("M54.5",), treatment=text) is None
    assert "제4조" in reason


# --- 주사: 약제 분류는 서술로 가르지 않는다 ---


@pytest.mark.parametrize(
    "text", ["항암주사 통원", "싸이모신 항암주사", "글루타치온 항암제 주사", "항생제주사"]
)
def test_an_injection_called_anticancer_still_goes_to_a_person(text) -> None:
    """항암제 주사는 (1)(2)지만, "항암주사"라는 낱말이 약관의 항암제를 보증하지 않는다."""
    reason = why_no_rule(SEVERE, ("C50.9",), abroad=None, treatment=text)

    assert find_rule(SEVERE, None, ("C50.9",), treatment=text) is None
    assert "항암제ㆍ항생제ㆍ희귀의약품 주사면 (1)(2)" in reason


# --- 표지가 아닌 것 ---


@pytest.mark.parametrize(
    "text",
    ["요관결석 체외충격파쇄석술", "충격파 쇄석술", "통원 빈도수가 늘었다", "보험 claims 정리"],
)
def test_lookalikes_are_not_markers(text) -> None:
    """쇄석술(결석)은 제2조의 체외충격파치료가 아니고, '빈도수'의 도수는 도수치료가 아니다."""
    assert noncovered_items(text) == {}


def test_products_without_item_rules_are_unaffected() -> None:
    """급여 MRI는 급여 의료비다 — 급여 실손은 항목을 따로 두지 않는다."""
    rule = find_rule(BENEFIT, None, ("M54.5",), treatment="허리 MRI 촬영")

    assert rule is not None and rule.coverage == "(2)질병급여"
    assert carved_out_items(BENEFIT, "허리 MRI 촬영") == {}


# --- 사유에 적는 낱말 ---


def test_the_reason_names_the_word_without_its_particle() -> None:
    assert noncovered_items("도수치료를 받음")[MUSCULOSKELETAL] == "도수치료"
    assert noncovered_items("허리 MRI를 찍음")[MRI] == "MRI"
    # 표지 자체는 깎지 않는다.
    assert noncovered_items("엠알아이 촬영")[MRI] == "엠알아이"


# --- 후속 질문: 청구에 없던 시술은 새 사실이다 ---


def test_a_follow_up_that_reveals_a_procedure_is_a_new_fact() -> None:
    claim = _claim(SEVERE, "2026-08-10 허리 통증 통원, 진료비 10만원")

    assert "시술" in detect_new_facts("사실 도수치료 받은 건데 지급액 얼마예요?", claim)


def test_a_follow_up_about_the_known_procedure_is_not_new() -> None:
    claim = _claim(SEVERE, REPRO_NARRATIVE)

    assert "시술" not in detect_new_facts("도수치료는 왜 안 되나요?", claim)


def test_a_follow_up_about_mri_on_a_benefit_claim_is_not_new() -> None:
    claim = _claim(BENEFIT, "2026-08-10 허리 통증 통원, 진료비 10만원")

    assert "시술" not in detect_new_facts("MRI도 찍었는데 왜 이 금액이죠?", claim)


# --- 재심사: 서술을 고쳐 말해도 표지는 남는다 ---
#
# 반박 검증: 도수치료 청구(HUMAN_REVIEW)를 서술만 고쳐 다시 심사하면 표지가 사라져
# PARTIAL 84,000원이 나왔다. 서류 코드(`stated_codes`)를 서술로 지우지 못하게 한 것과
# 같은 이유로 막는다 — 정정이 맞는지는 서술만으로 알 수 없다.

CORRECTED = "2026-08-10 허리 통증 통원, 진료비 12만원으로 정정"


def _revise(claim, narrative: str = "", **kwargs):
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.mcp_server.server import _revise as revise

    return revise(claim, -1, -1, -1, -1, narrative, **kwargs)


def test_a_corrected_narrative_keeps_the_earlier_marker() -> None:
    revised = _revise(_claim(SEVERE, REPRO_NARRATIVE), CORRECTED)

    assert "도수치료" not in revised.narrative
    assert revised.held_treatment == ("도수치료",)
    assert find_rule(SEVERE, None, revised.diagnosis_codes, treatment=revised.treatment) is None


def test_a_corrected_narrative_is_still_held_end_to_end(monkeypatch) -> None:
    revised = _revise(_claim(SEVERE, REPRO_NARRATIVE), CORRECTED)
    result = _adjudicate(monkeypatch, revised)

    assert result.decision is Decision.HUMAN_REVIEW
    assert "'도수치료'" in result.reason


def test_the_held_marker_survives_later_revisions() -> None:
    once = _revise(_claim(SEVERE, REPRO_NARRATIVE), CORRECTED)
    by_amount = _revise(once, claimed_amount=150_000)
    by_narrative = _revise(once, "2026-08-10 허리 통증 통원, 진료비 13만원")

    assert by_amount.held_treatment == ("도수치료",)
    assert by_narrative.held_treatment == ("도수치료",)


def test_a_narrative_that_still_names_the_procedure_holds_nothing_extra() -> None:
    revised = _revise(_claim(SEVERE, REPRO_NARRATIVE), "2026-08-10 도수치료 통원, 진료비 12만원")

    assert revised.held_treatment == ()


def test_a_new_marker_in_the_correction_is_read_and_the_old_one_kept() -> None:
    revised = _revise(_claim(SEVERE, REPRO_NARRATIVE), "2026-08-10 무릎 MRI 통원, 진료비 60만원")

    assert revised.held_treatment == ("도수치료",)
    assert set(carved_out_items(SEVERE, revised.treatment)) == {MUSCULOSKELETAL, MRI}


def test_products_without_item_rules_hold_nothing() -> None:
    claim = _claim(BENEFIT, "2026-08-10 허리 MRI 통원, 진료비 10만원")

    assert _revise(claim, CORRECTED).held_treatment == ()


def test_revise_claim_says_which_marker_it_kept(monkeypatch) -> None:
    pytest.importorskip("mcp", reason="mcp extra가 없으면 건너뛴다")
    from clausegraph.agents import orchestrator
    from clausegraph.mcp_server import server

    _fake_graph(monkeypatch, orchestrator)
    monkeypatch.setattr(server, "driver", lambda: None)
    monkeypatch.setattr(server, "principal", Principal.everything)
    claim = _claim(SEVERE, REPRO_NARRATIVE)
    first = orchestrator.adjudicate(None, claim, principal=Principal.everything())
    session = server.STORE.open(claim, first, "첫 판정")

    reply = server.revise_claim(session.session_id, narrative=CORRECTED)

    assert "판정 HUMAN_REVIEW   지급액 0원" in reply
    assert "앞 서술의 표지 '도수치료'" in reply


# --- 항목 이름이 규칙과 어긋나지 않는다 ---


def test_every_item_route_names_an_existing_rule_with_a_limit() -> None:
    for (product, item), coverage in _ITEM_RULES.items():
        rule = _BY_KEY.get((product, coverage))
        assert rule is not None, f"{product}/{item}가 가리키는 규칙 {coverage}가 없다"
        assert rule.annual_limit is not None, f"{coverage}의 한도가 비었다 — 사유에 적을 수 없다"
