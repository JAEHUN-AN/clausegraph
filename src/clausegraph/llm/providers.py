"""재 볼 대상 세 단. 같은 프롬프트를 **모델 급만 바꿔** 흘린다.

notes/012는 Qwen3-4B 하나로 재고 "더 큰 모델은 다를 수 있다"고 적어 뒀다.
그 문장이 남아 있는 한 "규칙 표를 쓰기로 했다"는 결정은 측정이 아니라
추정이다. 이 파일은 그 빈칸을 메우려고 있다(notes/031).

## 왜 하필 세 단인가

    4B 로컬     — 지금 가진 것
    오픈웨이트  — 폐쇄망에 **들여놓을 수 있는** 상한
    프론티어    — 이 일의 상한선. 들여놓을 수는 없다

가운데 단이 핵심이다. 보험사는 대개 망분리라 프론티어 API를 부를 수 없다.
프론티어가 잘한다는 사실만으로는 결정이 바뀌지 않고, **온프렘에 올릴 수 있는
모델이 잘하는가**가 바뀌는 조건이다. 그래서 급을 하나로 뭉뚱그리지 않고
"들여놓을 수 있는가"를 단마다 들고 다닌다.

## 키

무료 티어만 쓴다. 키가 없는 단은 목록에서 빠지고, 하나도 없으면 규칙 표만
측정한다 — 예전과 같은 동작이다. 키는 `.env`에 두고 저장소에 올리지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .client import LlmClient

# 무료 티어의 분당 상한에 맞춘 호출 간격. 넉넉하게 잡는다 — 한 번 429를
# 맞으면 백오프로 더 오래 기다리게 되므로 미리 벌리는 쪽이 빠르다.
GROQ_INTERVAL_SEC = 2.5
GEMINI_INTERVAL_SEC = 6.5

# 기본 모델. 무료 티어에서 부를 수 있는 것 중 각 단을 대표하는 것으로 골랐다.
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


@dataclass(frozen=True)
class Backend:
    """한 단.

    `onprem`은 폐쇄망에 들여놓을 수 있는가다. 결론을 쓸 때 정확도와 함께
    봐야 하는 값이라 측정 대상에 붙여 둔다.
    """

    key: str
    label: str
    onprem: bool
    client: LlmClient


def _local() -> Backend:
    client = LlmClient.from_env()
    model = client.model or "Qwen3-4B-Q4_K_M"
    return Backend(key="local", label=f"로컬 {model} (CPU)", onprem=True, client=client)


def _groq(api_key: str) -> Backend:
    model = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    return Backend(
        key="groq",
        label=f"오픈웨이트 {model}",
        # 가중치가 공개된 모델이다. 여기서는 남의 서버로 부르지만, 같은
        # 가중치를 사내 GPU에 올릴 수 있다 — 그래서 온프렘으로 센다.
        onprem=True,
        client=LlmClient(
            base_url=GROQ_BASE_URL,
            model=model,
            api_key=api_key,
            local=False,
            min_interval_sec=GROQ_INTERVAL_SEC,
        ),
    )


def _gemini(api_key: str) -> Backend:
    model = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    return Backend(
        key="gemini",
        label=f"프론티어 {model}",
        onprem=False,
        client=LlmClient(
            base_url=GEMINI_BASE_URL,
            model=model,
            api_key=api_key,
            local=False,
            # 2.5 계열은 기본이 사고 모드다. 끄지 않으면 96토큰을 생각에 다
            # 쓰고 본문이 비어서 온다 — 로컬에서 `enable_thinking: false`로
            # 껐던 것과 같은 문제이고, 공급자마다 손잡이 이름만 다르다.
            extra_body={"reasoning_effort": "none"},
            min_interval_sec=GEMINI_INTERVAL_SEC,
        ),
    )


def discover() -> tuple[Backend, ...]:
    """환경에 준비된 단만 모은다. 순서는 작은 모델부터다."""
    backends = [_local()]

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        backends.append(_groq(groq_key))

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        backends.append(_gemini(gemini_key))

    return tuple(backends)
