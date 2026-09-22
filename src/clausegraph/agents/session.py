"""심사 대화 한 건의 상태.

한 번 판정하고 끝나는 도구는 실제 심사 창구를 흉내 내지 못한다. 판정이
나오면 반드시 다음 말이 온다 — *"왜 안 되는데요"*, *"뭘 내면 되나요"*,
*"작년에 받은 게 있는데 그럼 달라지나요"*.

## 무엇을 기억하는가

**대화 글이 아니라 판정의 구조를 기억한다.** 앞 턴의 답변 문장을 들고
다니면서 거기에 이어 말하면, 두 턴만 지나도 인용이 원본에서 떠내려간다.
이 저장소가 들고 있는 것은 `Claim`과 `Adjudication`이고 둘 다 frozen이다.
후속 질문은 **그 구조에서 결정론적으로** 답한다(`followup.py`).

## 기억의 위험 — 낡은 답

multi-turn에서 가장 위험한 것은 잊는 것이 아니라 **낡은 것을 그대로 말하는
것**이다.

    턴1  "충치 임플란트 120만원"        -> DENIED
    턴2  "아 작년에 이미 300만원 받았어요"

턴2는 판정의 입력을 바꾼다. 기억해 둔 DENIED를 근거로 답하면 **이미 틀린
판정을 자신 있게 설명하게 된다.** 그래서 새 사실이 들어온 턴은 기억에서
답하지 않고 다시 심사한다 — 그 판단은 `followup.py`가 한다.

## 계약 상태의 저장소가 아니다

`ClaimHistory`가 그렇듯 여기도 아니다. 세션은 **대화가 살아 있는 동안의
작업 기억**이고, 프로세스 안에 둔다. 폐쇄망 전제라 외부 세션 저장소를
가정하지 않고, 심사 한 건이 수 ms라 왕복 비용이 측정 대상보다 커진다.
그래서 수명과 개수에 상한을 건다 — 없으면 프로세스가 대화를 영원히 들고
있게 된다.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import Adjudication, Claim

# 창구 대화 하나의 수명. 넘으면 지운다 — 어제 하던 이야기에 오늘 이어
# 답하면 그 사이 바뀐 것을 모르는 채로 말하게 된다.
SESSION_TTL_SEC = 1800.0
# 동시에 들고 있을 대화 수. 넘으면 가장 오래 손대지 않은 것부터 버린다.
MAX_SESSIONS = 64
# 한 대화의 턴 상한. 이보다 길어지면 새 청구로 시작하는 게 맞다.
MAX_TURNS = 40


class TurnKind(StrEnum):
    """그 턴이 무엇이었는가."""

    ADJUDICATE = "ADJUDICATE"
    FOLLOW_UP = "FOLLOW_UP"
    REVISE = "REVISE"


class Turn(BaseModel):
    """대화 한 턴. 무엇을 물었고 무엇으로 답했는가."""

    model_config = {"frozen": True}

    index: int
    kind: TurnKind
    question: str
    answer: str
    # 그 턴이 본 청구의 판(版). 재심사할 때마다 올라간다. 답변이 어느
    # 판을 근거로 했는지 되짚을 수 있어야 한다.
    revision: int


class Session(BaseModel):
    """심사 대화 하나."""

    model_config = {"frozen": True}

    session_id: str
    claim: Claim
    adjudication: Adjudication
    revision: int = 0
    turns: tuple[Turn, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0

    def with_turn(
        self,
        kind: TurnKind,
        question: str,
        answer: str,
        *,
        now: float,
        claim: Claim | None = None,
        adjudication: Adjudication | None = None,
    ) -> Session:
        """턴을 하나 붙인 새 세션을 만든다. 갈아 끼우지 않는다.

        `claim`과 `adjudication`을 함께 주면 재심사한 턴이다 — 판이 올라간다.
        """
        revised = claim is not None or adjudication is not None
        revision = self.revision + 1 if revised else self.revision
        turn = Turn(
            index=len(self.turns) + 1,
            kind=kind,
            question=question,
            answer=answer,
            revision=revision,
        )
        return self.model_copy(
            update={
                "claim": claim or self.claim,
                "adjudication": adjudication or self.adjudication,
                "revision": revision,
                # 오래된 턴부터 흘려보낸다. 최근 것이 답에 쓰인다.
                "turns": (*self.turns, turn)[-MAX_TURNS:],
                "updated_at": now,
            }
        )


class SessionStore:
    """살아 있는 대화들. 수명과 개수에 상한이 있다.

    `clock`을 받는 것은 시험 때문만이 아니다. 만료를 실제 시계에 묶어 두면
    "30분 뒤에 지워지는가"를 확인할 방법이 없어진다.
    """

    def __init__(
        self,
        *,
        ttl_sec: float = SESSION_TTL_SEC,
        max_sessions: int = MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_sec
        self._max = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def open(self, claim: Claim, adjudication: Adjudication, answer: str) -> Session:
        """판정 하나로 대화를 연다."""
        now = self._clock()
        self._evict_expired(now)
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            claim=claim,
            adjudication=adjudication,
            created_at=now,
            updated_at=now,
        ).with_turn(TurnKind.ADJUDICATE, claim.narrative, answer, now=now)
        self._sessions[session.session_id] = session
        self._evict_overflow()
        return session

    def get(self, session_id: str) -> Session | None:
        """살아 있으면 돌려준다. 만료됐으면 None — 되살리지 않는다."""
        now = self._clock()
        self._evict_expired(now)
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions.move_to_end(session_id)
        return session

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self._sessions.move_to_end(session.session_id)
        self._evict_overflow()

    def close(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        self._evict_expired(self._clock())
        return len(self._sessions)

    def _evict_expired(self, now: float) -> None:
        stale = [
            key
            for key, session in self._sessions.items()
            if now - session.updated_at > self._ttl
        ]
        for key in stale:
            del self._sessions[key]

    def _evict_overflow(self) -> None:
        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)


# 프로세스 하나에 하나. MCP 서버가 쓴다.
STORE = SessionStore()


class SessionSummary(BaseModel):
    """대화 상태를 사람에게 보여 줄 때 쓰는 요약."""

    model_config = {"frozen": True}

    session_id: str
    revision: int
    turns: int
    decision: str
    product: str
    fields_known: tuple[str, ...] = Field(
        default=(), description="청구에 채워진 값 — 무엇을 더 받으면 되는지 보이게"
    )


def summarize(session: Session) -> SessionSummary:
    claim = session.claim
    known = []
    if claim.diagnosis_codes:
        known.append("진단코드")
    if claim.claimed_amount:
        known.append("청구금액")
    if claim.hospital_days:
        known.append("입원일수")
    if claim.history is not None:
        known.append("올해누적")
    if claim.institution:
        known.append("의료기관")
    if claim.copay_rate is not None:
        known.append("본인부담률")
    return SessionSummary(
        session_id=session.session_id,
        revision=session.revision,
        turns=len(session.turns),
        decision=str(session.adjudication.decision),
        product=claim.product,
        fields_known=tuple(known),
    )
