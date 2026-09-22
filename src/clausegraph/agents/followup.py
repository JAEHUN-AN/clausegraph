"""후속 질문 — 판정이 나온 뒤에 오는 말들.

판정 하나를 내놓고 끝나는 창구는 없다. 반드시 다음이 온다.

    "왜 안 되는데요"          -> 저장된 근거 조항으로 답한다
    "뭘 내면 되나요"          -> 무엇이 없어서 못 정했는지 이름으로 답한다
    "금액이 왜 이건가요"      -> 계산이 선 자리와 못 선 자리를 가른다
    "작년에 300만원 받았어요" -> **답하지 않고 다시 심사한다**

## 규칙으로 가른다

질문 분류에 LLM을 쓰지 않는다. notes/031에서 본 대로 이 규모의 분류는
규칙이 더 빠르고 틀리는 방식이 예측 가능하다. 무엇보다 **못 알아들었을 때
지어내지 않는다** — `UNKNOWN`은 정직한 답이고, 지어낸 분류는 엉뚱한 조항을
인용하게 만든다.

## 가장 위험한 분류는 NEW_FACTS다

새 사실이 섞인 질문을 `WHY`로 읽으면, **이미 낡은 판정을 근거까지 붙여
자신 있게 설명하게 된다.** 틀린 답 중에 제일 나쁜 종류다. 그래서 새 사실
검사가 다른 모든 분류보다 먼저 온다 — *"작년에 300만원 받았는데 왜
부지급이죠"* 는 `WHY`가 아니라 `NEW_FACTS`다.

숫자가 있다고 무조건 새 사실은 아니다. *"120만원이 왜 안 나와요"* 의
120만원은 이미 청구에 있는 값이다. **지금 아는 값과 달라야** 새 사실이다.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .extract import parse_amounts
from .models import Adjudication, Claim, Decision
from .session import Session

# 사실추출과 같은 표기를 본다. 여기서 값을 뽑으려는 게 아니라
# "판정의 입력이 바뀌는가"만 가리면 된다.
_KCD_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
_DAYS_RE = re.compile(r"(\d{1,3})\s*일\s*(?:간\s*)?입원")
_DATE_RE = re.compile(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")

# 올해 누적을 말하는 표현. 금액과 함께 와야 새 사실로 본다.
_HISTORY_WORDS = ("이미 받", "기지급", "작년", "올해", "지난번", "전에 받", "누적")

_WHY_WORDS = ("왜", "이유", "어째서", "무슨 근거", "납득", "때문")
_DOCS_WORDS = ("서류", "무엇을 내", "뭘 내", "뭐 내", "제출", "보완", "추가로 필요")
_AMOUNT_WORDS = ("금액", "얼마", "지급액", "계산", "얼마나")
_CLAUSE_WORDS = ("조항", "약관", "몇 조", "어느 조", "근거 조")


class FollowUp(StrEnum):
    WHY = "WHY"
    DOCS = "DOCS"
    AMOUNT = "AMOUNT"
    CLAUSE = "CLAUSE"
    NEW_FACTS = "NEW_FACTS"
    UNKNOWN = "UNKNOWN"


def detect_new_facts(question: str, claim: Claim) -> tuple[str, ...]:
    """이 질문이 판정의 입력을 바꾸는가. 바꾸는 항목의 이름을 돌려준다.

    **지금 아는 값과 다를 때만** 새 사실로 센다. 이미 아는 값을 되물은
    것까지 재심사로 보내면, 사용자는 같은 답을 두 번 받으면서 매번
    "다시 심사했다"는 말을 듣게 된다.
    """
    found: list[str] = []

    # "300만원"과 "3,000,000원"은 같은 값이다. 표기가 달라도 같은 값이면
    # 새 사실이 아니다 — `parse_amounts`가 단위를 풀어 준다.
    amounts = set(parse_amounts(question))
    known_amounts = _known_amounts(claim)
    fresh_amounts = amounts - known_amounts - {0}

    if fresh_amounts and any(word in question for word in _HISTORY_WORDS):
        # 누적은 청구 금액과 다른 자리에 들어간다. 먼저 가른다.
        found.append("올해누적")
    elif fresh_amounts:
        found.append("청구금액")

    codes = {code for code in _KCD_RE.findall(question.upper())}
    if codes - set(claim.diagnosis_codes):
        found.append("진단코드")

    days = _DAYS_RE.search(question)
    if days and int(days.group(1)) != claim.hospital_days:
        found.append("입원일수")

    if _DATE_RE.search(question) and not _same_known_date(question, claim):
        found.append("일자")

    return tuple(found)


def _known_amounts(claim: Claim) -> set[int]:
    """이 청구가 **이미 본** 금액들.

    `claimed_amount`만 비교하면 안 된다. 청구 모델은 금액을 하나만 들고
    있는데 사람의 말에는 여러 개가 나온다 — *"차량가액 300만원인데 수리비
    400만원을 청구"* 에서 청구에 담기는 것은 앞의 하나뿐이다. 뒤의 값을
    새 사실로 세면, 방금 한 말을 되풀이했을 뿐인데 재심사로 보낸다.
    실제 분쟁 문장 160건 중 6건이 이 모양이었다(notes/033).

    그래서 **청구가 만들어진 서술에 있던 금액 전부**를 아는 값으로 센다.
    이미 한 번 말한 값은 새 사실이 아니다.
    """
    known = {claim.claimed_amount, claim.room_charge}
    known.update(parse_amounts(claim.narrative))
    if claim.history is not None:
        known.update(
            {
                claim.history.paid_this_year,
                claim.history.self_paid_this_year,
            }
        )
    return known


def _same_known_date(question: str, claim: Claim) -> bool:
    match = _DATE_RE.search(question)
    if match is None:
        return True
    year, month, day = (int(part) for part in match.groups())
    stamp = f"{year:04d}-{month:02d}-{day:02d}"
    known = {str(claim.enrolled_on)}
    if claim.incident_on is not None:
        known.add(str(claim.incident_on))
    return stamp in known


def classify(question: str, claim: Claim) -> FollowUp:
    """무엇을 묻는 말인가. 새 사실 검사가 가장 먼저다."""
    if detect_new_facts(question, claim):
        return FollowUp.NEW_FACTS

    text = question.strip()
    # 조항을 콕 집어 묻는 것이 "왜"보다 좁다. 좁은 것부터 본다.
    if any(word in text for word in _CLAUSE_WORDS):
        return FollowUp.CLAUSE
    if any(word in text for word in _DOCS_WORDS):
        return FollowUp.DOCS
    if any(word in text for word in _AMOUNT_WORDS):
        return FollowUp.AMOUNT
    if any(word in text for word in _WHY_WORDS):
        return FollowUp.WHY
    return FollowUp.UNKNOWN


def answer(session: Session, question: str) -> tuple[FollowUp, str]:
    """저장된 판정에서 답한다. 재심사가 필요하면 그렇게 말한다."""
    kind = classify(question, session.claim)
    handlers = {
        FollowUp.NEW_FACTS: _answer_new_facts,
        FollowUp.WHY: _answer_why,
        FollowUp.CLAUSE: _answer_clause,
        FollowUp.DOCS: _answer_docs,
        FollowUp.AMOUNT: _answer_amount,
        FollowUp.UNKNOWN: _answer_unknown,
    }
    return kind, handlers[kind](session, question)


def _answer_new_facts(session: Session, question: str) -> str:
    fields = detect_new_facts(question, session.claim)
    return (
        f"이 말에는 판정을 바꾸는 값이 들어 있다 — {', '.join(fields)}.\n"
        f"앞의 판정({session.adjudication.decision})은 그 값을 모르고 낸 것이라"
        " 그대로 설명하지 않는다.\n"
        "`revise_claim`으로 값을 넣어 다시 심사할 것."
    )


def _answer_why(session: Session, _question: str) -> str:
    result = session.adjudication
    lines = [f"판정 {result.decision} — {result.reason}"]
    lines.extend(_cite(result, ("exclusion", "coverage")))
    if result.guardrails:
        lines.append(f"발동한 가드레일: {', '.join(result.guardrails)}")
    failed = [step for step in result.steps if not step.ok]
    if failed:
        lines.append(f"막힌 자리: {failed[0].step} — {failed[0].summary}")
    return "\n".join(lines)


def _answer_clause(session: Session, _question: str) -> str:
    result = session.adjudication
    if not result.evidence:
        return (
            "인용할 조항이 없다. 근거 조항을 특정하지 못하면 결론을 내지 않으므로"
            f" 이 판정({result.decision})은 조항에서 나온 것이 아니다."
        )
    lines = [f"적용 약관 {result.applied_version}"]
    lines.extend(_cite(result, ("exclusion", "exception", "coverage"), limit=6))
    return "\n".join(lines)


def _answer_docs(session: Session, _question: str) -> str:
    result = session.adjudication
    if result.decision is Decision.NEEDS_DOCS:
        return f"아직 정해지지 않았다 — {result.reason}\n이 값을 확인해 다시 넣을 것."
    if result.decision is Decision.HUMAN_REVIEW:
        return "\n".join(["기계가 정할 일이 아니라고 보고 넘긴 건이다.", *_missing(result)])
    if result.decision is Decision.DENIED:
        return "\n".join(
            [
                "부지급은 서류가 없어서가 아니라 약관 면책에 걸려서다."
                " 서류를 더 낸다고 뒤집히지 않는다.",
                *_exception_hint(result),
            ]
        )
    return f"판정 {result.decision}이고 더 받을 서류는 없다."


def _answer_amount(session: Session, _question: str) -> str:
    result = session.adjudication
    if result.amount:
        bound = ""
        if "amount_upper_bound" in result.guardrails:
            bound = " (이 값은 지급액이 아니라 **상한**이다)"
        elif "amount_lower_bound" in result.guardrails:
            bound = " (이 값은 지급액이 아니라 **하한**이다)"
        return f"{result.amount:,}원{bound}\n{result.reason}"
    return "\n".join(["지급액을 계산하지 못했다.", *_missing(result)])


def _answer_unknown(session: Session, _question: str) -> str:
    return (
        "무엇을 묻는지 가리지 못했다. 지어내 답하지 않는다.\n"
        f"지금 대화가 들고 있는 것: {session.claim.product} /"
        f" 가입 {session.claim.enrolled_on} / 판정 {session.adjudication.decision}\n"
        "판정 이유·근거 조항·필요 서류·지급액 중에서 물어볼 것."
    )


def _cite(result: Adjudication, roles: tuple[str, ...], limit: int = 3) -> list[str]:
    picked = [item for item in result.evidence if item.role in roles][:limit]
    return [
        f"  [{item.role}] 제{item.article_number}조({item.article_title}) {item.quote}"
        f"\n    {item.node_uid}"
        for item in picked
    ]


def _missing(result: Adjudication) -> list[str]:
    """무엇이 없어서 못 정했는지. 이름까지 돌려주는 것이 이 시스템의 약속이다."""
    lines = [f"사유: {result.reason}"]
    if result.guardrails:
        lines.append(f"발동한 가드레일: {', '.join(result.guardrails)}")
    return lines


def _exception_hint(result: Adjudication) -> list[str]:
    """면책에 예외가 달려 있으면 그것을 짚는다.

    면책 조항의 상당수가 `다만`으로 예외를 달고 다른 조문을 가리킨다
    (notes/022). "면책이다"까지만 말하고 끝내면 청구인에게 중요한 뒷부분을
    빠뜨리는 것이다.
    """
    exceptions = [item for item in result.evidence if item.role == "exception"]
    if not exceptions:
        return []
    lines = ["다만 이 면책에는 예외가 달려 있다 — 해당되는지 확인할 것:"]
    lines.extend(
        f"  제{item.article_number}조({item.article_title}) {item.quote}"
        f"\n    {item.node_uid}"
        for item in exceptions[:3]
    )
    return lines
