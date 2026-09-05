"""Tier A 라벨러 — 분쟁조정사례에서 지급/부지급 라벨을 규칙으로 뽑는다.

처리결과는 "(보험사가 한 행위)가 부당한가"라는 형태로 서술된다. 그래서
결론은 한 단어가 아니라 **행위 × 부당 여부**의 곱이다.

어려운 지점은 부정이 긍정 어구 *뒤에* 붙어 절 전체를 뒤집는다는 것이다.

    "지급하지 않은 업무처리가 부당하다고 판단"            -> 지급
    "지급하지 않은 업무처리가 부당하다고 보기 어렵다고"    -> 부지급
    "부당하다고 판단하기는 어려움을 안내"                 -> 부지급
    "질병입원일당 보험금을 지급하도록 권고하기 어려움"     -> 부지급

`부당`이나 `지급하도록`을 앞에서 잡으면 정답이 정확히 뒤집힌다. 그래서
술어를 찾은 뒤 **그 뒤 30자(부정 윈도우)** 를 보고 뒤집힘을 판정한다.
이 프로젝트의 주제(면책 = 부정 조건)와 같은 구조의 문제다.

제목과 처리결과는 서로 다른 것을 말한다 — 제목은 소비자가 겪은 상황,
처리결과는 금감원의 판단이다. 두 신호로 따로 뽑아 어긋나면 UNKNOWN이
아니라 NEEDS_REVIEW로 남긴다. 규칙이 손대지 못한 지점을 감추지 않기 위해서다.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import DisputeCase

# 약관 지식그래프로 근거를 댈 수 있는 유형만 대상으로 한다.
# 자동차보험·손해보험(운전자/책임/재물)은 표준약관 범위 밖 (notes/001).
IN_SCOPE_PREFIXES = ("실손보험", "질병·상해·간병보험", "생명보험", "보험(일반)")

OUTCOME_SECTION = "처리결과"


class Label(StrEnum):
    PAID = "PAID"
    DENIED = "DENIED"
    PARTIAL = "PARTIAL"
    NOT_CLAIM = "NOT_CLAIM"
    NEEDS_DOCS = "NEEDS_DOCS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    HIGH = "HIGH"      # 제목과 처리결과가 같은 답을 냈다
    MEDIUM = "MEDIUM"  # 한쪽만 걸렸다
    NONE = "NONE"


# --- 처리결과 ---
# 판정 술어. 이것만으로는 방향을 알 수 없고, 뒤에 붙는 부정을 함께 봐야 한다.
_VERDICT = re.compile(r"부당(?:하다고|하지|한|하다)")
_UPHELD_DIRECT = re.compile(r"타당하다고|적정하(?:게|다고)")

# 지급을 명령·권고하는 술어 — 역시 뒤의 부정에 지배된다.
_PAY_ORDER = re.compile(
    r"지급하도록\s*권고|지급\s*?권고|지급하기로|지급하도록|지급받게\s*되|"
    r"지급할\s*필요가\s*있|지급하여야|지급\s*의무가\s*있"
)

# 지급 여부가 아직 정해지지 않은 사안 — 서류가 갖춰지면 다시 본다.
# 이 프로젝트의 판정 체계에 있는 "추가서류요청"에 대응한다.
_NEEDS_DOCS = re.compile(r"추가\s*서류\s*제출을\s*요구|서류\s*제출을\s*요구")

# 술어 뒤 이 범위 안에 부정이 있으면 절이 뒤집힌다.
_NEGATION = re.compile(r"어렵|어려움|않|아니|없|못")
NEGATION_WINDOW = 30

# 판단 술어가 없는 사례의 직접 서술
_DENY_DIRECT = re.compile(
    r"지급\s*어려움|지급이?\s*어렵|지급하기\s*어렵|지급\s*대상(?:에\s*해당하지\s*않|이\s*아니)"
)
_PARTIAL_DIRECT = re.compile(r"일부만\s*지급|일부\s*지급")

# 보험사가 한 행위 — 지급 거절인가, 계약 처리인가
_ACTION_DENIED_CLAIM = re.compile(r"보험금[^.]{0,20}지급하지\s*않|지급하지\s*않은\s*업무")
_ACTION_CONTRACT = re.compile(r"해지한\s*업무|반환하지\s*않은\s*업무|해지\s*처리")

# --- 제목 신호 ---
# 제목은 결론을 마지막에 둔다("지급 거절 후 재검토를 거쳐 지급받은 사례").
# 그래서 가장 뒤에 걸린 신호를 택한다.
_TITLE_DENIED = re.compile(r"지급받지\s*못한|받지\s*못한|부지급|지급\s*거절|거절된|공제한")
_TITLE_PAID = re.compile(r"지급받은|지급받게|지급받음|인정된\s*사례")
_TITLE_PARTIAL = re.compile(r"일부만|적은\s*금액|일부\s*지급|감액된|삭감된|에\s*대해서만")

# 보험금 지급 판정이 아닌 사안 — 계약 유지·보험료·절차 문제
_TITLE_NOT_CLAIM = re.compile(
    r"해지된|부활|할증|전환\s*신청|인수가?\s*거절|재개|할인을?\s*적용|개시연령|"
    r"서류\s*제출을\s*요구|미동의|보험료\s*반환|원금손실|만기환급금|배당"
)


class LabeledCase(BaseModel):
    """규칙이 매긴 라벨과 그 근거."""

    model_config = {"frozen": True}

    case_slno: int
    cvpl: str
    title: str
    label: Label
    confidence: Confidence
    title_signal: Label
    outcome_signal: Label
    evidence: str = Field(default="", description="처리결과에서 규칙이 걸린 구절")


def is_in_scope(case: DisputeCase | dict) -> bool:
    """약관 지식그래프로 다룰 수 있는 유형인가."""
    cvpl = case.ref.cvpl if isinstance(case, DisputeCase) else case["ref"]["cvpl"]
    return cvpl.startswith(IN_SCOPE_PREFIXES)


def label_from_title(title: str) -> Label:
    """제목에서 라벨을 뽑는다. 여러 신호가 걸리면 가장 뒤에 있는 것을 택한다."""
    if _TITLE_NOT_CLAIM.search(title):
        return Label.NOT_CLAIM
    # 일부지급은 지급/부지급과 경쟁하는 결론이 아니라 지급 형태를 말하는
    # 수식어다("감액된 보험금을 지급받은 사례"). 그래서 위치와 무관하게 우선한다.
    if _TITLE_PARTIAL.search(title):
        return Label.PARTIAL

    best_at, best_label = -1, Label.UNKNOWN
    for pattern, label in (
        (_TITLE_DENIED, Label.DENIED),
        (_TITLE_PAID, Label.PAID),
    ):
        for match in pattern.finditer(title):
            if match.start() > best_at:
                best_at, best_label = match.start(), label
    return best_label


def _is_negated(text: str, at: int) -> bool:
    """술어 뒤 부정 윈도우를 본다 — 부정이 절 전체를 뒤집는다."""
    return _NEGATION.search(text[at : at + NEGATION_WINDOW]) is not None


def _last(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """결론은 뒤에 온다. 마지막 일치를 쓴다."""
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def label_from_outcome(outcome: str) -> tuple[Label, str]:
    """처리결과에서 라벨과 근거 구절을 뽑는다."""
    if not outcome.strip():
        return Label.UNKNOWN, ""

    if (docs := _last(_NEEDS_DOCS, outcome)) is not None:
        return Label.NEEDS_DOCS, _quote(outcome, docs.start())

    verdict = _last(_VERDICT, outcome)
    if verdict is not None:
        upheld = _is_negated(outcome, verdict.end())
        evidence = _quote(outcome, verdict.start())
        if _ACTION_CONTRACT.search(outcome) and not _ACTION_DENIED_CLAIM.search(outcome):
            return Label.NOT_CLAIM, evidence
        # 지급 거절이 부당하다 -> 지급, 부당하다고 보기 어렵다 -> 부지급
        return (Label.DENIED if upheld else Label.PAID), evidence

    if (direct := _last(_UPHELD_DIRECT, outcome)) is not None:
        return Label.DENIED, _quote(outcome, direct.start())

    if (order := _last(_PAY_ORDER, outcome)) is not None:
        negated = _is_negated(outcome, order.end())
        return (Label.DENIED if negated else Label.PAID), _quote(outcome, order.start())

    for pattern, label in ((_PARTIAL_DIRECT, Label.PARTIAL), (_DENY_DIRECT, Label.DENIED)):
        if (match := _last(pattern, outcome)) is not None:
            return label, _quote(outcome, match.start())
    return Label.UNKNOWN, ""


def label_case(case: DisputeCase) -> LabeledCase:
    title_signal = label_from_title(case.ref.title)
    outcome_signal, evidence = label_from_outcome(case.sections.get(OUTCOME_SECTION, ""))
    label, confidence = _combine(title_signal, outcome_signal)
    return LabeledCase(
        case_slno=case.case_slno,
        cvpl=case.ref.cvpl,
        title=case.ref.title,
        label=label,
        confidence=confidence,
        title_signal=title_signal,
        outcome_signal=outcome_signal,
        evidence=evidence,
    )


def _combine(title_signal: Label, outcome_signal: Label) -> tuple[Label, Confidence]:
    """두 신호를 합친다. 어긋나면 감추지 않고 NEEDS_REVIEW로 남긴다."""
    if title_signal == outcome_signal:
        return (title_signal, Confidence.HIGH if title_signal != Label.UNKNOWN else Confidence.NONE)
    if title_signal == Label.UNKNOWN:
        return outcome_signal, Confidence.MEDIUM
    if outcome_signal == Label.UNKNOWN:
        return title_signal, Confidence.MEDIUM
    # NOT_CLAIM은 사안 자체의 성격이라 한쪽만 봐도 우선한다.
    if Label.NOT_CLAIM in (title_signal, outcome_signal):
        return Label.NOT_CLAIM, Confidence.MEDIUM
    # NEEDS_DOCS는 "아직 정해지지 않았다"는 단계의 진술이라 결론보다 우선한다.
    if Label.NEEDS_DOCS in (title_signal, outcome_signal):
        return Label.NEEDS_DOCS, Confidence.MEDIUM
    # 감액·삭감 사안(4건). 처리결과의 판단은 다투는 잔여분을 향하고,
    # 지급의 형태는 일부 지급이다. 두 신호가 서로 다른 것을 말하고 있다.
    if {title_signal, outcome_signal} == {Label.PARTIAL, Label.DENIED}:
        return Label.PARTIAL, Confidence.MEDIUM
    return Label.NEEDS_REVIEW, Confidence.NONE


def _quote(text: str, at: int, span: int = 45) -> str:
    return " ".join(text[max(0, at - span) : at + span].split())
