"""국가법령정보 공동활용 API 클라이언트.

인증키(OC)는 호출 URL에 그대로 실려 나가므로 비밀키가 아니다. 다만 신청자
식별에 쓰이니 환경변수로 둔다.

본문 조회는 응답이 크다(보험업감독업무시행세칙 한 건이 4~5MB). 표준약관은
이 본문 XML의 `<별표내용>` 안에 통째로 들어 있어, 별표 HWP를 따로 받을
필요가 없다 (notes/003).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

BASE_URL = "https://www.law.go.kr/DRF"
USER_AGENT = "clausegraph/0.1 (portfolio research)"

# 현행/연혁 구분 — 목록 조회 파라미터
CURRENT = 1
HISTORICAL = 2

DEFAULT_DELAY_SEC = 0.5
DEFAULT_TIMEOUT_SEC = 120
MAX_ATTEMPTS = 4
MAX_DISPLAY = 100


class MissingOcError(RuntimeError):
    """인증키가 없다. open.law.go.kr에서 신청 후 .env의 LAW_API_OC에 넣는다."""


@dataclass(frozen=True)
class LawClient:
    oc: str
    delay_sec: float = DEFAULT_DELAY_SEC
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    session: requests.Session | None = None

    @classmethod
    def from_env(cls, **kwargs: object) -> LawClient:
        oc = os.environ.get("LAW_API_OC", "").strip()
        if not oc:
            raise MissingOcError(
                "LAW_API_OC가 비어 있다 — open.law.go.kr에서 발급 후 .env에 넣을 것"
            )
        return cls(oc=oc, **kwargs)  # type: ignore[arg-type]

    def search_admrul(self, query: str, nw: int, page: int = 1) -> str:
        """행정규칙 목록. nw=1 현행, nw=2 연혁."""
        return self._get(
            "lawSearch.do",
            {
                "target": "admrul",
                "type": "XML",
                "query": query,
                "nw": str(nw),
                "display": str(MAX_DISPLAY),
                "page": str(page),
            },
        )

    def fetch_admrul(self, seq: int) -> str:
        """행정규칙 본문 XML. 별표내용까지 포함된다."""
        return self._get("lawService.do", {"target": "admrul", "type": "XML", "ID": str(seq)})

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, str]) -> str:
        import time

        session = self.session or requests.Session()
        session.headers.setdefault("User-Agent", USER_AGENT)
        response = session.get(
            f"{BASE_URL}/{path}", params={"OC": self.oc, **params}, timeout=self.timeout_sec
        )
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        time.sleep(self.delay_sec)
        return response.text
