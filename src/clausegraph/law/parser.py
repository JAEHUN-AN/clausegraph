"""행정규칙 XML에서 표준약관을 꺼낸다.

`<별표내용>`은 원문 한 줄이 CDATA 조각 하나로 들어 있다(별표15는 18,000조각
남짓). 조각을 줄바꿈으로 이어붙이면 표 안의 텍스트까지 살아 있는 평문이 된다.

HWP 첨부(`별표서식파일링크`)로도 같은 문서를 받을 수 있지만 그 경로는 쓰지
않는다 — pyhwp가 표 내용을 `<표>` 자리표시자로 버려서, 장해분류표·수술분류표
같은 **지급 판정의 핵심 표가 통째로 사라진다** (notes/003).
"""

from __future__ import annotations

import re

from .models import STANDARD_TERMS_BYEOLPYO_NO, AdmRulRef, StandardTerms

_LIST_ROW_RE = re.compile(
    r"<admrul id=\"\d+\">[\s\S]*?"
    r"<행정규칙일련번호>(\d+)</행정규칙일련번호>[\s\S]*?"
    r"<행정규칙명><!\[CDATA\[(.*?)\]\]></행정규칙명>[\s\S]*?"
    r"<발령일자>(\d+)</발령일자>[\s\S]*?"
    r"<현행연혁구분>(\S+?)</현행연혁구분>"
)
_TOTAL_RE = re.compile(r"<totalCnt>(\d+)</totalCnt>")
_RESULT_MSG_RE = re.compile(r"<resultMsg>(.*?)</resultMsg>")

_BYEOLPYO_UNIT_RE = re.compile(r"<별표단위[^>]*>[\s\S]*?</별표단위>")
_BYEOLPYO_NO_RE = re.compile(r"<별표번호>(\d+)</별표번호>")
_BYEOLPYO_TITLE_RE = re.compile(r"<별표제목><!\[CDATA\[(.*?)\]\]></별표제목>")
_BYEOLPYO_BODY_RE = re.compile(r"<별표내용>([\s\S]*?)</별표내용>")
_CDATA_RE = re.compile(r"<!\[CDATA\[([\s\S]*?)\]\]>")

_EFFECTIVE_RE = re.compile(r"<시행일자>(\d+)</시행일자>")
_PROMULGATED_RE = re.compile(r"<발령일자>(\d+)</발령일자>")
_SEQ_RE = re.compile(r"<행정규칙일련번호>(\d+)</행정규칙일련번호>")

# 신청하지 않은 API를 부르면 200에 에러 페이지가 온다 — 조용히 넘기지 않는다.
_NOT_SUBSCRIBED = "미신청"


class LawApiError(RuntimeError):
    """API가 XML 대신 오류 페이지를 돌려줬다."""


def parse_admrul_list(xml: str) -> list[AdmRulRef]:
    _guard(xml)
    return [
        AdmRulRef(seq=int(seq), name=name, promulgated_on=promulgated, status=status)
        for seq, name, promulgated, status in _LIST_ROW_RE.findall(xml)
    ]


def parse_total(xml: str) -> int:
    _guard(xml)
    match = _TOTAL_RE.search(xml)
    if not match:
        raise LawApiError("목록 응답에 totalCnt가 없다")
    return int(match.group(1))


def extract_standard_terms(
    xml: str, byeolpyo_no: str = STANDARD_TERMS_BYEOLPYO_NO
) -> StandardTerms:
    """본문 XML에서 표준약관 별표를 꺼낸다."""
    _guard(xml)

    unit = _find_byeolpyo(xml, byeolpyo_no)
    if unit is None:
        raise LawApiError(f"별표번호 {byeolpyo_no}(표준약관)을 찾지 못했다")

    body = _BYEOLPYO_BODY_RE.search(unit)
    if body is None:
        raise LawApiError(f"별표번호 {byeolpyo_no}에 별표내용이 없다")

    title = _BYEOLPYO_TITLE_RE.search(unit)
    return StandardTerms(
        admrul_seq=int(_require(_SEQ_RE, xml, "행정규칙일련번호")),
        effective_on=_require(_EFFECTIVE_RE, xml, "시행일자"),
        promulgated_on=_require(_PROMULGATED_RE, xml, "발령일자"),
        title=title.group(1) if title else "",
        text="\n".join(_CDATA_RE.findall(body.group(1))),
    )


def _find_byeolpyo(xml: str, byeolpyo_no: str) -> str | None:
    for unit in _BYEOLPYO_UNIT_RE.findall(xml):
        number = _BYEOLPYO_NO_RE.search(unit)
        if number and number.group(1) == byeolpyo_no:
            return unit
    return None


def _require(pattern: re.Pattern[str], xml: str, label: str) -> str:
    match = pattern.search(xml)
    if not match:
        raise LawApiError(f"본문 XML에 {label}가 없다")
    return match.group(1)


def _guard(xml: str) -> None:
    if xml.lstrip().startswith("<?xml"):
        return
    message = _RESULT_MSG_RE.search(xml)
    if message:
        raise LawApiError(f"API 오류: {message.group(1)}")
    if _NOT_SUBSCRIBED in xml:
        raise LawApiError("미신청 API — open.law.go.kr에서 해당 target을 활용신청할 것")
    raise LawApiError("XML이 아닌 응답을 받았다")
