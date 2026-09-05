"""표준약관 수집 도메인 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field

# 보험업감독업무시행세칙 별표15 = 표준약관(제5-13조제1항관련)
STANDARD_TERMS_BYEOLPYO_NO = "0015"
ADMRUL_NAME = "보험업감독업무시행세칙"


class AdmRulRef(BaseModel):
    """행정규칙 목록 한 행 — 본문을 받기 전의 식별자."""

    model_config = {"frozen": True}

    seq: int = Field(description="행정규칙일련번호 — 본문 조회 키")
    name: str
    promulgated_on: str = Field(description="발령일자 YYYYMMDD")
    status: str = Field(description="현행연혁구분 — 현행 또는 연혁")


class StandardTerms(BaseModel):
    """한 시행일자의 표준약관 전문."""

    model_config = {"frozen": True}

    admrul_seq: int
    effective_on: str = Field(description="시행일자 YYYYMMDD")
    promulgated_on: str
    title: str
    text: str = Field(description="별표내용 CDATA를 이어붙인 평문")

    @property
    def char_count(self) -> int:
        return len(self.text)
