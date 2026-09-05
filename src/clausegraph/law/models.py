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


class Subitem(BaseModel):
    """목 — '가. 나. 다.'"""

    model_config = {"frozen": True}

    label: str
    text: str


class Item(BaseModel):
    """호 — '1. 2. 3.' 면책 사유가 이 단위로 열거된다."""

    model_config = {"frozen": True}

    number: int
    text: str
    subitems: tuple[Subitem, ...] = ()


class Paragraph(BaseModel):
    """항 — '① ② ③'. 항 표기가 없는 조문은 암묵적으로 1항 하나다."""

    model_config = {"frozen": True}

    number: int
    text: str
    items: tuple[Item, ...] = ()
    implicit: bool = Field(
        default=False, description="원문에 항 표기가 없어 통째로 1항으로 담았는지"
    )


class Article(BaseModel):
    """조 — 그래프의 기본 노드.

    조문 번호는 표준약관 섹션마다 새로 시작한다(화재보험 제4조와
    자동차보험 제4조는 다른 조문이다). 그래서 식별자는 번호가 아니라
    (섹션, 하위구분, 번호)의 조합이다.
    """

    model_config = {"frozen": True}

    section: str = Field(description="□ 표준약관 이름 — 생명보험, 손해보험 등")
    subsection: str | None = Field(
        default=None, description="<화재보험> 등 손해보험 안의 상품 구분"
    )
    chapter: str | None = Field(default=None, description="제N관 또는 제N절")
    number: str = Field(description="조문 번호 — '5' 또는 '5의2'")
    title: str
    text: str = Field(description="조문 전문 (항·호·목 포함)")
    paragraphs: tuple[Paragraph, ...] = ()
    revised_on: tuple[str, ...] = Field(
        default=(), description="<개정>·<신설> 표기에서 뽑은 날짜 YYYY-MM-DD"
    )

    @property
    def unit(self) -> str:
        """이 조문이 속한 약관 상품.

        본문의 물리적 배치가 목차와 어긋나는 구간이 있어(배상책임보험 등이
        해외여행 실손 뒤에 `□` 없이 나온다) `section`만으로는 소속을 못 정한다.
        가장 구체적인 표기를 소속으로 본다.
        """
        return self.subsection or self.section

    @property
    def key(self) -> str:
        return f"{self.unit}/제{self.number}조"


class TermsDocument(BaseModel):
    """한 시행일자 표준약관의 파싱 결과."""

    model_config = {"frozen": True}

    effective_on: str
    admrul_seq: int
    articles: tuple[Article, ...]
    sections: tuple[str, ...]


class TableExclusion(BaseModel):
    """표 안에 적힌 면책 사유 하나.

    실손 계열은 면책을 조문 문장이 아니라 표로 적는다. 같은 조문 안에서도
    보장종목((1)상해급여, (2)질병급여 …)마다 사유가 다르므로, 조문 밑의
    호로만 두면 어느 보장에 걸리는 사유인지 잃는다.
    """

    model_config = {"frozen": True}

    coverage: str = Field(description="보장종목 — '(1)상해급여' 등")
    paragraph: int = Field(description="항 번호. 항 표기가 없으면 1")
    number: int = Field(description="호 번호")
    text: str
