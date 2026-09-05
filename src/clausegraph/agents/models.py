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
    narrative: str = Field(default="", description="청구인이 적은 사유")


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
