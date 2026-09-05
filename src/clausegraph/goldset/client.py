"""FSS 분쟁조정사례 HTTP 클라이언트.

공개 게시판이고 인증이 없다. 서버 렌더 HTML이라 브라우저 자동화가 필요 없다.
공공 사이트이므로 요청 간 지연을 기본값으로 둔다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

BASE_URL = "https://www.fss.or.kr/fss/job/fncCnflCase"
MENU_NO = "201195"
USER_AGENT = "clausegraph/0.1 (portfolio research; contact via github.com/JAEHUN-AN)"

DEFAULT_DELAY_SEC = 0.5
DEFAULT_TIMEOUT_SEC = 30
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class FssClient:
    """목록/상세 HTML을 가져온다. 파싱은 하지 않는다."""

    delay_sec: float = DEFAULT_DELAY_SEC
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    session: requests.Session | None = None

    def _session(self) -> requests.Session:
        if self.session is not None:
            return self.session
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        return session

    def fetch_list(self, page: int, rgnl_code: str | None = None) -> str:
        params = {"menuNo": MENU_NO, "pageIndex": str(page)}
        if rgnl_code:
            params["rgnlCode"] = rgnl_code
        return self._get(f"{BASE_URL}/list.do", params)

    def fetch_view(self, case_slno: int) -> str:
        params = {"caseSlno": str(case_slno), "menuNo": MENU_NO}
        return self._get(f"{BASE_URL}/view.do", params)

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _get(self, url: str, params: dict[str, str]) -> str:
        response = self._session().get(url, params=params, timeout=self.timeout_sec)
        response.raise_for_status()
        # 서버가 charset을 안 주는 경우가 있어 명시한다.
        response.encoding = response.encoding or "utf-8"
        time.sleep(self.delay_sec)
        return response.text
