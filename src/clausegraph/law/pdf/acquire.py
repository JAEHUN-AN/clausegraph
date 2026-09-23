"""표준약관 PDF를 만든다 — 공식 첨부(HWP)를 받아 조판본으로 바꾼다.

## 원천이 PDF를 주지 않는다

국가법령정보의 별표서식 API(`admbyl`)는 첨부를 **HWP로만** 준다. PDF 링크
필드가 있는 쪽은 법령 별표(`licbyl`)고, 행정규칙 별표에는 없다. 몇 가지
PDF 경로를 찔러 봤지만 전부 같은 HWP로 되돌아온다(notes/036).

그래서 받은 HWP를 한글로 열어 PDF로 저장한다. **새 문서를 만드는 게 아니라
같은 파일을 조판해 내보내는 것**이고, 보험사가 약관 PDF를 배포하는 경로와
같다.

## 이 단계는 환경에 묶인다

Windows + 한글(HWP) 설치가 필요하다. 수집 스크립트 중 유일하게 이식되지
않는 부분이라 따로 떼어 뒀다. 이미 PDF가 있으면 이 모듈은 부르지 않는다.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from ..client import LawClient
from ..models import ADMRUL_NAME

STANDARD_TERMS_BYEOLPYO_NO = "001500"
STANDARD_TERMS_MARKER = "표준약관"
DOWNLOAD_BASE = "https://www.law.go.kr"

_ROW_RE = re.compile(r"<admrulbyl id=\"\d+\">([\s\S]*?)</admrulbyl>")
_FIELD_RE = {
    "seq": re.compile(r"<별표일련번호>(\d+)</별표일련번호>"),
    "number": re.compile(r"<별표번호>(\d+)</별표번호>"),
    "name": re.compile(r"<별표명><!\[CDATA\[([\s\S]*?)\]\]></별표명>"),
    "link": re.compile(r"<별표서식파일링크>([^<]+)</별표서식파일링크>"),
    "admrul_seq": re.compile(r"<현행연혁행정규칙일련번호>(\d+)</현행연혁행정규칙일련번호>"),
}


class AcquireError(RuntimeError):
    pass


@dataclass(frozen=True)
class ByeolpyoRef:
    seq: int
    number: str
    name: str
    link: str
    admrul_seq: int

    @property
    def url(self) -> str:
        return f"{DOWNLOAD_BASE}{self.link}"


def parse_byeolpyo_list(xml: str) -> list[ByeolpyoRef]:
    refs: list[ByeolpyoRef] = []
    for block in _ROW_RE.findall(xml):
        values = {}
        for field, pattern in _FIELD_RE.items():
            match = pattern.search(block)
            if match is None:
                break
            values[field] = match.group(1).strip()
        else:
            refs.append(
                ByeolpyoRef(
                    seq=int(values["seq"]),
                    number=values["number"],
                    name=values["name"],
                    link=values["link"],
                    admrul_seq=int(values["admrul_seq"]),
                )
            )
    return refs


def find_standard_terms(refs: list[ByeolpyoRef]) -> ByeolpyoRef:
    """별표15 중 표준약관을 고른다.

    별표번호 `001500`이 두 건 나온다 — `등록사항 변경 신고서`와 `표준약관`.
    번호만 보고 첫 건을 집으면 신고서 서식을 받는다.
    """
    matches = [
        ref
        for ref in refs
        if ref.number == STANDARD_TERMS_BYEOLPYO_NO and STANDARD_TERMS_MARKER in ref.name
    ]
    if not matches:
        raise AcquireError("별표15 표준약관을 목록에서 찾지 못했다")
    return matches[0]


def download(ref: ByeolpyoRef, dest: Path, *, session: requests.Session | None = None) -> Path:
    session = session or requests.Session()
    response = session.get(ref.url, timeout=300, headers={"User-Agent": "clausegraph/0.1"})
    response.raise_for_status()
    if not response.content.startswith(b"\xd0\xcf\x11\xe0"):
        raise AcquireError(f"HWP(OLE) 파일이 아니다 — {response.headers.get('Content-Type')}")
    dest.write_bytes(response.content)
    return dest


def convert_to_pdf(hwp_path: Path, pdf_path: Path) -> Path:
    """한글로 열어 PDF로 저장한다. Windows + 한글이 있어야 한다."""
    try:
        import win32com.client as win32
    except ImportError as error:  # pragma: no cover - 환경 의존
        raise AcquireError("pywin32가 필요하다 — uv pip install pywin32") from error

    hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    with contextlib.suppress(Exception):  # 보안 모듈이 없으면 대화상자가 뜰 수 있다
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
    hwp.XHwpWindows.Item(0).Visible = False
    if not hwp.Open(str(hwp_path.resolve()), "HWP", "forceopen:true"):
        raise AcquireError(f"한글이 파일을 열지 못했다: {hwp_path}")
    hwp.SaveAs(str(pdf_path.resolve()), "PDF")
    hwp.Quit()
    if not pdf_path.exists():
        raise AcquireError(f"PDF가 만들어지지 않았다: {pdf_path}")
    return pdf_path


def fetch_list(client: LawClient) -> list[ByeolpyoRef]:
    """행정규칙명으로 별표 목록을 받는다(119건, 2쪽)."""
    refs: list[ByeolpyoRef] = []
    for page in (1, 2):
        refs.extend(parse_byeolpyo_list(client.search_admbyl(ADMRUL_NAME, page=page)))
    return refs
