"""심사 도메인 모델.

판정은 네 값이다. `NEEDS_DOCS`는 "아직 정해지지 않았다"는 뜻이고,
`HUMAN_REVIEW`는 "기계가 정할 일이 아니다"는 뜻이다. 이 둘을 부지급으로
뭉개면 안 된다 — 지급 거절과 판단 보류는 소비자에게 전혀 다른 일이다.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Decision(StrEnum):
    PAID = "PAID"
    DENIED = "DENIED"
    PARTIAL = "PARTIAL"
    NEEDS_DOCS = "NEEDS_DOCS"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Claim(BaseModel):
    """청구 한 건. 사실추출이 채운다."""

    model_config = {"frozen": True}

    claim_id: str
    product: str = Field(description="가입 상품 — 약관 상품명")
    enrolled_on: date = Field(description="가입일. 적용 약관 버전을 가른다")
    incident_on: date | None = Field(default=None, description="사고·진단일")
    diagnosis_codes: tuple[str, ...] = Field(default=(), description="KCD 코드")
    procedure: str | None = Field(default=None, description="수술·시술명")
    hospital_days: int = Field(default=0, description="입원일수")
    claimed_amount: int = Field(default=0, description="청구 금액(원)")
    institution: str | None = Field(
        default=None,
        description="진료 의료기관 종류. 급여 통원 공제의 정액이 여기서 갈린다",
    )
    copay_rate: float | None = Field(
        default=None,
        description="건강보험 본인부담률. 영수증에서 오는 값이며 약관에 없다",
    )
    history: ClaimHistory | None = Field(
        default=None,
        description="올해 누적. 없으면 지급액이 아니라 상한만 말할 수 있다",
    )
    room_charge: int = Field(
        default=0,
        description=(
            "청구한 비급여 병실료(원). 상급병실료 차액은 제3조 표의 별도 행이라"
            " 입원의료비와 따로 계산한다. 0이면 상급병실료를 청구하지 않은 것이다"
        ),
    )
    narrative: str = Field(default="", description="청구인이 적은 사유")


class ClaimHistory(BaseModel):
    """그 계약·그 보장종목의 **올해 누적**. 보험사 시스템이 준다.

    이 시스템은 계약 상태의 저장소가 아니다. 연간한도는 이미 지급한 금액을
    빼야 하고 통원 횟수 한도는 이미 쓴 횟수를 알아야 하는데, 둘 다 여기
    없는 값이다.

    **없으면 0으로 두지 않는다.** 0으로 두면 모든 청구를 그 해 첫 청구로
    보게 되고, 그건 조용한 과다지급이다(notes/027).
    """

    model_config = {"frozen": True}

    paid_this_year: int = Field(default=0, description="그 보장종목의 올해 기지급액(원)")
    outpatient_visits_this_year: int = Field(
        default=0, description="그 보장종목의 올해 통원 횟수(특약2는 일수)"
    )
    self_paid_this_year: int = Field(
        default=0,
        description="올해 자기부담 누적(원). 급여 입원의 200만원 상한 판정에 쓴다",
    )


class Evidence(BaseModel):
    """판정의 근거. 조항을 가리키지 못하면 근거가 아니다."""

    model_config = {"frozen": True}

    node_uid: str = Field(description="Article 또는 Item의 uid")
    product: str
    article_number: str
    article_title: str
    quote: str = Field(description="인용 구절")
    role: str = Field(description="이 근거가 무엇을 뒷받침하는가 — coverage | exclusion")


class StepResult(BaseModel):
    """에이전트 한 스텝의 결과. 관측을 위해 남긴다."""

    model_config = {"frozen": True}

    step: str
    ok: bool
    summary: str
    elapsed_ms: float
    evidence: tuple[Evidence, ...] = ()
    detail: dict[str, object] = Field(default_factory=dict)


class Adjudication(BaseModel):
    """최종 판정."""

    model_config = {"frozen": True}

    claim_id: str
    decision: Decision
    amount: int = 0
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()
    applied_version: str | None = Field(
        default=None, description="가입 시점에 적용되던 약관 버전"
    )
    steps: tuple[StepResult, ...] = ()
    guardrails: tuple[str, ...] = Field(
        default=(), description="발동한 가드레일 이름"
    )

    @property
    def total_ms(self) -> float:
        return sum(step.elapsed_ms for step in self.steps)
