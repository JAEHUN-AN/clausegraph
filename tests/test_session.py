"""대화 상태와 후속 질문.

여기서 막고 싶은 사고는 하나로 모인다 — **낡은 판정을 자신 있게 설명하는
것.** 새 사실이 들어온 턴을 "왜"로 읽으면 이미 틀린 결론에 근거 조항까지
붙여서 답하게 된다. 틀린 답 중에 제일 나쁜 종류다.
"""

from __future__ import annotations

from datetime import date

import pytest

from clausegraph.agents.followup import FollowUp, answer, classify, detect_new_facts
from clausegraph.agents.models import (
    Adjudication,
    Claim,
    ClaimHistory,
    Decision,
    Evidence,
    StepResult,
)
from clausegraph.agents.session import (
    MAX_TURNS,
    SessionStore,
    TurnKind,
    summarize,
)


def make_claim(**overrides: object) -> Claim:
    defaults: dict[str, object] = {
        "claim_id": "C1",
        "product": "실손의료보험 특별약관1(중증 비급여 실손의료비)",
        "enrolled_on": date(2026, 7, 1),
        "diagnosis_codes": ("K02",),
        "claimed_amount": 1_200_000,
        "narrative": "충치가 심해 임플란트를 했습니다. 비급여 1,200,000원 청구합니다.",
    }
    return Claim(**(defaults | overrides))  # type: ignore[arg-type]


def make_result(**overrides: object) -> Adjudication:
    defaults: dict[str, object] = {
        "claim_id": "C1",
        "decision": Decision.DENIED,
        "reason": "진단코드 K02가 약관이 정한 면책 범위에 든다",
        "applied_version": "20260506",
        "evidence": (
            Evidence(
                node_uid="20260506/특약1/제4조#(3)",
                product="실손의료보험 특별약관1(중증 비급여 실손의료비)",
                article_number="4",
                article_title="보상하지 않는 사항",
                quote="치과치료(K00~K08)",
                role="exclusion",
            ),
        ),
    }
    return Adjudication(**(defaults | overrides))  # type: ignore[arg-type]


def open_session(store: SessionStore | None = None, **claim_kwargs: object):
    store = store or SessionStore()
    return store, store.open(make_claim(**claim_kwargs), make_result(), "판정 DENIED")


# --- 새 사실을 새 사실로 알아보는가 -----------------------------------------


def test_history_amount_is_a_new_fact() -> None:
    # 이걸 놓치면 한도를 다 쓴 계약에 앞의 판정을 그대로 설명하게 된다.
    fields = detect_new_facts("작년에 이미 3,000,000원 받았는데요", make_claim())

    assert fields == ("올해누적",)


def test_known_amount_is_not_a_new_fact() -> None:
    # 이미 아는 값을 되물었을 뿐이다. 재심사로 보내면 같은 답을 두 번 주면서
    # 매번 "다시 심사했다"고 말하게 된다.
    assert detect_new_facts("1,200,000원이 왜 안 나오나요", make_claim()) == ()


def test_unknown_code_is_a_new_fact() -> None:
    assert detect_new_facts("S82 골절도 같이 있었습니다", make_claim()) == ("진단코드",)


def test_known_code_is_not_a_new_fact() -> None:
    assert detect_new_facts("K02 때문인가요", make_claim()) == ()


def test_hospital_days_change_is_a_new_fact() -> None:
    assert detect_new_facts("사실 4일 입원했습니다", make_claim()) == ("입원일수",)


def test_known_enrolment_date_is_not_a_new_fact() -> None:
    assert detect_new_facts("2026-07-01 가입 맞습니다", make_claim()) == ()


# --- 분류 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("왜 부지급인가요", FollowUp.WHY),
        ("어째서 안 되는 건가요", FollowUp.WHY),
        ("서류를 뭘 내면 되나요", FollowUp.DOCS),
        ("추가로 필요한 게 있나요", FollowUp.DOCS),
        ("지급액이 얼마인가요", FollowUp.AMOUNT),
        ("어느 조항 때문인가요", FollowUp.CLAUSE),
        ("약관 몇 조인가요", FollowUp.CLAUSE),
        ("오늘 날씨 어때요", FollowUp.UNKNOWN),
    ],
)
def test_classify(question: str, expected: FollowUp) -> None:
    assert classify(question, make_claim()) is expected


def test_new_facts_beat_why() -> None:
    # 핵심 회귀 방지. "왜"가 들어 있어도 새 사실이 있으면 기억에서 답하지
    # 않는다 — 낡은 판정에 근거까지 붙여 설명하는 것을 막는다.
    question = "작년에 3,000,000원 받았는데 왜 부지급이죠"

    assert classify(question, make_claim()) is FollowUp.NEW_FACTS


def test_new_facts_answer_refuses_to_explain_the_old_decision() -> None:
    _, session = open_session()

    kind, text = answer(session, "작년에 3,000,000원 받았는데 왜 부지급이죠")

    assert kind is FollowUp.NEW_FACTS
    assert "다시 심사" in text
    # 앞 판정을 근거로 설명하지 않는다.
    assert "치과치료" not in text


# --- 저장된 구조에서 답하는가 -----------------------------------------------


def test_why_cites_the_stored_evidence() -> None:
    _, session = open_session()

    kind, text = answer(session, "왜 부지급인가요")

    assert kind is FollowUp.WHY
    assert "치과치료(K00~K08)" in text
    assert "20260506/특약1/제4조#(3)" in text


def test_denied_does_not_promise_that_documents_help() -> None:
    # 부지급에 "서류를 내면 된다"고 답하면 청구인을 헛걸음시킨다.
    _, session = open_session()

    _, text = answer(session, "서류 뭘 내면 되나요")

    assert "면책" in text
    assert "뒤집히지 않는다" in text


def test_denied_points_at_the_exception_when_there_is_one() -> None:
    # 면책의 상당수가 `다만`으로 예외를 달고 다른 조문을 가리킨다(notes/022).
    # "면책이다"에서 끊으면 청구인에게 중요한 뒷부분을 빠뜨린다.
    store = SessionStore()
    result = make_result(
        evidence=(
            *make_result().evidence,
            Evidence(
                node_uid="20260506/특약1/제3조",
                product="실손의료보험 특별약관1(중증 비급여 실손의료비)",
                article_number="3",
                article_title="보장종목별 보상내용",
                quote="안면부 골절로 발생한 의료비는 보상합니다",
                role="exception",
            ),
        )
    )
    session = store.open(make_claim(), result, "판정 DENIED")

    _, text = answer(session, "서류 뭘 내면 되나요")

    assert "예외가 달려 있다" in text
    assert "안면부 골절" in text


def test_unknown_says_so_instead_of_guessing() -> None:
    _, session = open_session()

    kind, text = answer(session, "오늘 날씨 어때요")

    assert kind is FollowUp.UNKNOWN
    assert "지어내 답하지 않는다" in text


def test_amount_marks_an_upper_bound_as_a_bound() -> None:
    # 상한을 지급액으로 말하면 과다지급 약속이 된다.
    store = SessionStore()
    result = make_result(
        decision=Decision.HUMAN_REVIEW,
        amount=800_000,
        guardrails=("amount_upper_bound",),
    )
    session = store.open(make_claim(), result, "판정 HUMAN_REVIEW")

    _, text = answer(session, "얼마 나오나요")

    assert "상한" in text


# --- 저장소 -----------------------------------------------------------------


def test_session_expires() -> None:
    now = [0.0]
    store = SessionStore(ttl_sec=100.0, clock=lambda: now[0])
    session = store.open(make_claim(), make_result(), "판정")

    now[0] = 101.0

    assert store.get(session.session_id) is None


def test_expired_session_is_not_revived() -> None:
    # 어제 하던 이야기에 오늘 이어 답하면 그 사이 바뀐 것을 모르는 채로 말한다.
    now = [0.0]
    store = SessionStore(ttl_sec=10.0, clock=lambda: now[0])
    store.open(make_claim(), make_result(), "판정")
    now[0] = 50.0

    assert len(store) == 0


def test_oldest_session_is_evicted_over_the_cap() -> None:
    store = SessionStore(max_sessions=2)
    first = store.open(make_claim(), make_result(), "판정")
    store.open(make_claim(), make_result(), "판정")
    store.open(make_claim(), make_result(), "판정")

    assert store.get(first.session_id) is None
    assert len(store) == 2


def test_turn_does_not_mutate_the_session() -> None:
    # frozen 구조를 갈아 끼우지 않는다. 앞 턴이 무엇을 보고 답했는지
    # 되짚을 수 있어야 한다.
    _, session = open_session()

    later = session.with_turn(TurnKind.FOLLOW_UP, "왜요", "면책", now=1.0)

    assert len(session.turns) == 1
    assert len(later.turns) == 2


def test_revision_rises_only_when_re_adjudicated() -> None:
    _, session = open_session()

    asked = session.with_turn(TurnKind.FOLLOW_UP, "왜요", "면책", now=1.0)
    revised = asked.with_turn(
        TurnKind.REVISE, "3백만원 받았어요", "다시 심사", now=2.0,
        claim=make_claim(history=ClaimHistory(paid_this_year=3_000_000)),
        adjudication=make_result(decision=Decision.HUMAN_REVIEW),
    )

    assert asked.revision == 0
    assert revised.revision == 1
    assert revised.turns[-1].revision == 1


def test_turns_are_capped() -> None:
    _, session = open_session()
    for index in range(MAX_TURNS + 5):
        session = session.with_turn(TurnKind.FOLLOW_UP, f"q{index}", "a", now=1.0)

    assert len(session.turns) == MAX_TURNS


def test_summary_shows_what_is_known() -> None:
    # "무엇을 더 주면 답이 나오는가"가 보여야 창구에서 쓸 수 있다.
    store = SessionStore()
    session = store.open(
        make_claim(history=ClaimHistory(paid_this_year=100)), make_result(), "판정"
    )

    summary = summarize(session)

    assert "올해누적" in summary.fields_known
    assert "진단코드" in summary.fields_known
    assert summary.decision == "DENIED"


def test_steps_surface_where_it_stopped() -> None:
    store = SessionStore()
    result = make_result(
        decision=Decision.NEEDS_DOCS,
        reason="가입 시점의 약관을 특정하지 못했다",
        evidence=(),
        steps=(
            StepResult(
                step="보장탐색:버전확정",
                ok=False,
                summary="가입일 2019-01-01에 적용되던 약관을 찾지 못했다",
                elapsed_ms=1.0,
            ),
        ),
    )
    session = store.open(make_claim(), result, "판정 NEEDS_DOCS")

    _, text = answer(session, "왜 판정이 안 나오나요")

    assert "막힌 자리" in text
    assert "버전확정" in text


# --- MCP 글루: 값 갱신과 차이 ------------------------------------------------


def test_revise_keeps_values_that_were_not_given() -> None:
    # 주지 않은 값을 0으로 덮으면, 통원 횟수를 말하려던 사람이 기지급액을
    # 0으로 만들어 버린다 — 한도가 되살아나 과다지급이 된다.
    from clausegraph.mcp_server.server import _revise

    claim = make_claim(
        history=ClaimHistory(paid_this_year=3_000_000, outpatient_visits_this_year=7)
    )

    revised = _revise(claim, -1, 9, -1, -1, "")

    assert revised.history is not None
    assert revised.history.paid_this_year == 3_000_000
    assert revised.history.outpatient_visits_this_year == 9


def test_revise_accepts_zero_as_a_real_value() -> None:
    # 0은 "올해 아무것도 없다"이고 음수는 "주지 않았다"다.
    from clausegraph.mcp_server.server import _revise

    claim = make_claim(history=ClaimHistory(paid_this_year=3_000_000))

    revised = _revise(claim, 0, -1, -1, -1, "")

    assert revised.history is not None
    assert revised.history.paid_this_year == 0


def test_new_narrative_replaces_the_old_facts() -> None:
    # 청구인이 고쳐 말한 것이면 옛 코드가 남으면 안 된다. 덧붙이면
    # 취소한 진단이 면책을 계속 걸어 버린다.
    from clausegraph.mcp_server.server import _revise

    claim = make_claim(diagnosis_codes=("K02", "K08"))

    revised = _revise(claim, -1, -1, -1, -1, "J18 폐렴으로 4일간 입원했습니다")

    assert "K02" not in revised.diagnosis_codes
    assert "J18" in revised.diagnosis_codes
    assert revised.hospital_days == 4


def test_negation_in_a_revision_is_not_understood() -> None:
    """알려진 한계를 고정해 둔다 — 고친 줄 알고 넘어가지 않도록.

    용어집은 낱말을 본다. "충치가 **아니라** 폐렴"에서 `충치`를 그대로
    K02로 잡는다. 재심사는 사람이 "아니라"라고 말하는 자리라 이 약점이
    하필 여기서 커진다(notes/032).
    """
    from clausegraph.mcp_server.server import _revise

    revised = _revise(make_claim(), -1, -1, -1, -1, "충치가 아니라 J18 폐렴이었습니다")

    # 부정을 읽었다면 K02는 없어야 하지만, 지금은 남는다.
    assert "K02" in revised.diagnosis_codes


def test_diff_says_when_nothing_changed() -> None:
    # "다시 심사했다"만 말하고 같은 결과를 내놓으면 사용자는 뭐가 반영됐는지
    # 알 수 없다.
    from clausegraph.mcp_server.server import _diff

    assert "달라진 것이 없다" in _diff(make_result(), make_result())


def test_diff_reports_decision_and_guardrail_changes() -> None:
    from clausegraph.mcp_server.server import _diff

    before = make_result(decision=Decision.HUMAN_REVIEW, guardrails=("amount_upper_bound",))
    after = make_result(decision=Decision.PARTIAL, amount=500_000)

    text = _diff(before, after)

    assert "HUMAN_REVIEW -> PARTIAL" in text
    assert "풀린 가드레일 amount_upper_bound" in text
