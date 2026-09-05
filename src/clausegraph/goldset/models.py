"""분쟁조정사례 도메인 모델.

수집 단계는 원문을 그대로 보존한다. 지급/부지급 라벨링은 별도 단계이며
(`label`은 여기서 항상 None), 그 규칙의 정밀도는 수동 라벨로 따로 측정한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 권역 코드 — 목록 페이지 select[name=rgnlCode]
RGNL_INSURANCE = "B"
RGNL_BANK = "A"
RGNL_INVEST = "C"


class CaseRef(BaseModel):
    """목록 페이지 한 행. 상세 본문 없이 메타데이터만 담는다."""

    model_config = {"frozen": True}

    case_slno: int = Field(description="상세 페이지 식별자 (view.do?caseSlno=)")
    seq: int = Field(description="목록에 표시된 번호")
    rgnl: str = Field(description="권역 — 보험/은행ㆍ중소서민/금융투자")
    cvpl: str = Field(description="유형 — 실손보험(치료비), 자동차보험(대인) 등")
    title: str
    registered_on: str = Field(description="등록일 YYYY-MM-DD")


class DisputeCase(BaseModel):
    """상세 본문까지 채워진 사례."""

    model_config = {"frozen": True}

    ref: CaseRef
    sections: dict[str, str] = Field(
        default_factory=dict,
        description="'▣ 민원내용' 등 마커로 나뉜 본문. 마커가 없으면 빈 dict",
    )
    body_text: str = Field(description="섹션 분할 전 본문 평문 — 분할 실패 시 폴백")
    label: str | None = Field(
        default=None,
        description="지급/부지급 라벨. 수집 단계에서는 채우지 않는다",
    )

    @property
    def case_slno(self) -> int:
        return self.ref.case_slno
