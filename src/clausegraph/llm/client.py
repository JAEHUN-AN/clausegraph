"""LLM 클라이언트 — OpenAI 호환 `/v1/chat/completions`.

기본은 로컬이다. 폐쇄망 전제라 **심사 경로는 외부 API를 부르지 않는다.**
GPU가 없어 llama.cpp를 CPU에서 돌리고, OpenAI 호환 엔드포인트만 쓴다 —
나중에 vLLM이나 TensorRT-LLM으로 갈아타도 이 파일은 그대로다.

**측정할 때만 밖으로 나간다.** notes/012에서 4B가 3/10이었을 때 남은 질문은
"모델이 작아서인가, 이 일이 원래 LLM에 안 맞는 것인가"였다. 답하려면 같은
프롬프트를 더 큰 모델에 흘려 봐야 한다. 그래서 키와 공급자별 인자를 받을 수
있게 열어 뒀다(notes/031). 키를 주지 않으면 동작은 예전과 같다 — 헤더가
붙지 않는다.

`agents/`와 `mcp_server/`는 여전히 로컬만 본다. 이 파일이 열렸다고 심사가
밖으로 나가지는 않는다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_TIMEOUT_SEC = 120
HEALTH_TIMEOUT_SEC = 5

# 무료 티어는 분당 요청 수로 끊는다. 한 번 막히면 그 뒤가 전부 밀리므로
# 물러섰다가 다시 온다. 로컬에는 해당이 없어 기본 간격은 0이다.
MAX_ATTEMPTS = 4
RETRY_WAIT_MULTIPLIER_SEC = 2
RETRY_WAIT_MAX_SEC = 30
# 분당 창이 지나기를 기다린다. 창보다 짧게 물러서면 또 맞는다.
RATE_LIMIT_WAIT_SEC = 65
_BACKOFF = wait_exponential(
    multiplier=RETRY_WAIT_MULTIPLIER_SEC, max=RETRY_WAIT_MAX_SEC
)

# 코드만 뽑는 일이라 길 필요가 없다. 길게 두면 모델이 설명을 붙인다.
DEFAULT_MAX_TOKENS = 96
# 판정에 쓰는 값이라 흔들리면 안 된다.
DEFAULT_TEMPERATURE = 0.0


class LlmUnavailableError(RuntimeError):
    """LLM에 붙지 못했다. 규칙 경로로 내려가야 한다.

    `retryable`은 다시 걸어 볼 값어치가 있는 실패인지다. 끊긴 연결·시간
    초과·429·5xx는 기다리면 되지만, 400이나 401은 몇 번을 걸어도 같다.
    """

    def __init__(
        self, message: str, *, retryable: bool = False, rate_limited: bool = False
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, LlmUnavailableError) and exc.retryable


def _wait_policy(retry_state: object) -> float:
    """429는 창이 지나야 풀린다. 지수 백오프로는 같은 분 안에서 또 맞는다.

    무료 티어는 분당으로 끊으므로, 물러설 때 **창 하나를 통째로 비운다.**
    처음에 2초·4초로 물러섰다가 재시도가 같은 창에 요청을 더 쌓아 한도를
    넘기는 것을 봤다 — 재시도가 스스로 원인이 됐다.
    """
    outcome = getattr(retry_state, "outcome", None)
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, LlmUnavailableError) and exc.rate_limited:
        return RATE_LIMIT_WAIT_SEC
    return _BACKOFF(retry_state)


# 기다리면 풀리는 상태 코드. 429는 무료 티어의 분당 상한이다.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class LlmClient:
    """한 모델을 부르는 방법.

    `local`은 llama.cpp 전용 필드를 보낼지 가른다. 원격 API는 모르는 필드를
    받으면 400으로 튕기므로 조건 없이 보내면 안 된다.

    `extra_body`는 공급자마다 다른 손잡이다 — Gemini의 `reasoning_effort`처럼
    그쪽에만 있는 것. 여기 두면 공급자 지식이 `providers.py` 한 곳에 모인다.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    api_key: str = ""
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    local: bool = True
    # 사고 모델은 생각한 토큰도 여기서 깎는다. 96으로는 본문이 비어서 오는
    # 모델이 있어 단마다 다르게 준다 — 답을 담을 자리를 주는 것이지
    # 정확도를 올려 주는 손잡이가 아니다(notes/031).
    max_tokens: int = DEFAULT_MAX_TOKENS
    extra_body: dict[str, object] = field(default_factory=dict)
    min_interval_sec: float = 0.0
    session: requests.Session | None = None
    # 직전 호출 시각. frozen이라 값을 갈아 끼울 수 없어 한 칸짜리 리스트에 둔다.
    _last_call: list[float] = field(
        default_factory=lambda: [0.0], repr=False, compare=False
    )

    @classmethod
    def from_env(cls) -> LlmClient:
        """로컬 llama.cpp. 심사 경로가 쓰는 유일한 생성자다."""
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("LLM_MODEL", ""),
            timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _wait_turn(self) -> None:
        """분당 상한에 걸리지 않도록 호출 간격을 벌린다."""
        if self.min_interval_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_call[0]
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_call[0] = time.monotonic()

    def available(self) -> bool:
        """붙는가. 붙지 못하면 예외 대신 False — 규칙 경로로 내려간다."""
        try:
            response = (self.session or requests).get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=HEALTH_TIMEOUT_SEC,
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    @retry(
        # 전에는 `requests.RequestException`을 기다렸는데, 아래에서 그것을
        # `LlmUnavailableError`로 바꿔 던지고 있었다. 맞는 예외가 한 번도
        # 오지 않아 **재시도가 실제로는 걸리지 않았다.** 429를 만나기 전까지
        # 드러나지 않던 자리다.
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=_wait_policy,
        reraise=True,
    )
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        payload: dict[str, object] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        if self.local:
            # Qwen3는 기본이 사고 모드다. 코드 몇 개 뽑는 일에 수십 초를
            # 쓰므로 끈다. llama.cpp 전용 필드라 원격에는 보내지 않는다.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if self.model:
            payload["model"] = self.model
        payload.update(self.extra_body)

        self._wait_turn()
        try:
            response = (self.session or requests).post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            raise LlmUnavailableError(
                f"LLM 호출 실패 {status}: {exc}",
                retryable=status in _RETRYABLE_STATUS,
                rate_limited=status == 429,
            ) from exc
        except requests.RequestException as exc:
            # 끊긴 연결과 시간 초과. 상대가 잠깐 바쁜 것일 수 있다.
            raise LlmUnavailableError(f"LLM 호출 실패: {exc}", retryable=True) from exc

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LlmUnavailableError(f"응답에 choices가 없다: {body}")

        choice = choices[0]
        content = (choice.get("message") or {}).get("content", "") or ""
        if not content.strip():
            # 사고 모델이 배정된 토큰을 생각에 다 쓰면 본문이 빈 채로 온다
            # (finish_reason=length). 빈 문자열을 그대로 돌려주면 파서가
            # "코드 없음"으로 읽어 **모든 답이 NONE으로 채점된다.** 모델이
            # 틀린 것이 아니라 답을 못 받은 것이므로 실패로 올린다.
            raise LlmUnavailableError(
                f"본문이 비어 있다 (finish_reason={choice.get('finish_reason')})"
            )
        return content
