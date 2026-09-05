"""로컬 LLM 클라이언트 — OpenAI 호환 `/v1/chat/completions`.

폐쇄망 전제라 외부 API를 부르지 않는다. GPU가 없어 llama.cpp를 CPU에서
돌리고, OpenAI 호환 엔드포인트만 쓴다 — 나중에 vLLM이나 TensorRT-LLM으로
갈아타도 이 파일은 그대로다.

키가 없다. 로컬 서버라 인증할 상대가 없고, 있는 척 두면 폐쇄망에서 왜
붙지 않는지 찾기 어려워진다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_TIMEOUT_SEC = 120
MAX_ATTEMPTS = 2
RETRY_WAIT_SEC = 1

# 코드만 뽑는 일이라 길 필요가 없다. 길게 두면 모델이 설명을 붙인다.
DEFAULT_MAX_TOKENS = 96
# 판정에 쓰는 값이라 흔들리면 안 된다.
DEFAULT_TEMPERATURE = 0.0
HEALTH_TIMEOUT_SEC = 5


class LlmUnavailableError(RuntimeError):
    """로컬 LLM에 붙지 못했다. 규칙 경로로 내려가야 한다."""


@dataclass(frozen=True)
class LlmClient:
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    session: requests.Session | None = None

    @classmethod
    def from_env(cls) -> LlmClient:
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("LLM_MODEL", ""),
            timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)),
        )

    def available(self) -> bool:
        """서버가 떠 있는가. 붙지 못하면 예외 대신 False — 규칙 경로로 내려간다."""
        try:
            response = (self.session or requests).get(
                f"{self.base_url}/models", timeout=HEALTH_TIMEOUT_SEC
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_fixed(RETRY_WAIT_SEC),
        reraise=True,
    )
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        payload: dict[str, object] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Qwen3는 기본이 사고 모드다. 코드 몇 개 뽑는 일에 수십 초를
            # 쓰므로 끈다. 서버가 이 필드를 모르면 조용히 무시한다.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self.model:
            payload["model"] = self.model

        try:
            response = (self.session or requests).post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LlmUnavailableError(f"로컬 LLM 호출 실패: {exc}") from exc

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LlmUnavailableError(f"응답에 choices가 없다: {body}")
        return (choices[0].get("message") or {}).get("content", "") or ""
