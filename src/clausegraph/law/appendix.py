"""부칙에서 표준약관 적용례를 뽑는다.

## 왜 필요한가

지금까지 그래프는 세칙의 **시행일자**로만 버전을 갈랐다(notes/006). 그런데
부칙을 읽으면 약관의 적용은 그것과 다르다.

> 별표 14 및 별표 15의 개정규정은 **2003년 10월 1일부터 적용한다**
> 제5-13조 `<별표 15>` … 개정규정은 **2011. 4. 1. 이후 신계약부터 적용한다**
> 시행일 이후 **체결되는 보험계약부터** 적용할 수 있다

두 가지가 시행일자만으로는 안 잡힌다.

1. **적용일이 시행일과 다르다.** 세칙은 3월에 시행되고 약관은 4월부터
   적용될 수 있다.
2. **적용 대상이 신계약으로 한정된다.** 기존 계약에는 옛 약관이 그대로 남는다.

## 적용일을 단정하지 않는다

부칙 하나에 날짜가 여럿 나온다 — 세칙 시행일, 별표 적용일, 경과조치 기한이
한 문장에 섞인다. 어느 것이 "그 적용일"인지 기계가 고르면 틀린다.

그래서 **날짜를 후보로 모으고 원문을 함께 남긴다.** 적용 대상이 신계약으로
한정되는지는 표현이 분명해서 판정한다. 나머지는 심사자가 원문을 보고
정하도록 넘긴다 — 이 프로젝트가 계속 지켜 온 방식이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 부칙 블록과 그 공포일자를 짝으로 읽는다.
_APPENDIX_RE = re.compile(
    r"<부칙공포일자>(\d{8})</부칙공포일자>"
    r"<부칙공포번호>\d+</부칙공포번호>"
    r"<부칙내용\s*><!\[CDATA\[([\s\S]*?)\]\]></부칙내용>"
)

# 표준약관은 보험업감독업무시행세칙의 별표15다.
_STANDARD_TERMS_RE = re.compile(r"별표\s*15|별표15")

# `2003년 10월 1일`, `2011. 4. 1.` 두 표기가 섞여 있다.
_DATE_RE = re.compile(r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})")

# 적용 대상이 신계약으로 한정되는 표현.
_NEW_CONTRACTS_RE = re.compile(r"신계약|신규로\s*체결|체결되는\s*보험계약|이후\s*체결")

# 문장 경계. 법령문은 '…한다.'로 끝난다.
_SENTENCE_RE = re.compile(r"(?<=다\.)\s*")


@dataclass(frozen=True)
class Provision:
    """표준약관 적용례 하나."""

    promulgated_on: str
    new_contracts_only: bool
    candidate_dates: tuple[str, ...]
    text: str

    @property
    def summary(self) -> str:
        scope = "신계약부터" if self.new_contracts_only else "적용 대상 미확정"
        dates = ", ".join(self.candidate_dates) or "날짜 없음"
        return f"공포 {self.promulgated_on} / {scope} / 날짜 후보 {dates}"


def parse_provisions(admrul_xml: str) -> tuple[Provision, ...]:
    """행정규칙 본문 XML에서 표준약관 적용례를 뽑는다."""
    provisions: list[Provision] = []
    for promulgated_on, body in _APPENDIX_RE.findall(admrul_xml):
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_RE.split(body)
            if _STANDARD_TERMS_RE.search(sentence)
        ]
        if not sentences:
            continue

        joined = " ".join(sentences)
        dates = tuple(
            dict.fromkeys(
                f"{year}-{int(month):02d}-{int(day):02d}"
                for year, month, day in _DATE_RE.findall(joined)
            )
        )
        provisions.append(
            Provision(
                promulgated_on=promulgated_on,
                new_contracts_only=bool(_NEW_CONTRACTS_RE.search(joined)),
                candidate_dates=dates,
                text=" ".join(joined.split()),
            )
        )
    return tuple(provisions)


def applies_to_enrollment(provision: Provision, enrolled_on: str) -> bool | None:
    """가입일이 이 적용례의 대상인가. `enrolled_on`은 YYYYMMDD.

    `None`은 "판정할 수 없다"는 뜻이다. 날짜 후보가 여럿이면 어느 것을
    기준으로 삼아야 하는지 부칙만 보고는 정할 수 없다 — 단정하지 않고
    심사자에게 넘긴다.
    """
    if not provision.new_contracts_only:
        return None
    if len(provision.candidate_dates) != 1:
        return None

    threshold = provision.candidate_dates[0].replace("-", "")
    return enrolled_on >= threshold
