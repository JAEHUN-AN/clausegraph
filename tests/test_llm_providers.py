"""단을 고르는 규칙과, 단마다 다르게 나가야 하는 요청 모양.

여기서 막고 싶은 사고는 셋이다.

1. **심사 경로가 밖으로 나간다** — 폐쇄망 전제가 깨진다.
2. **llama.cpp 전용 필드를 원격에 보낸다** — 400으로 튕겨 측정이 통째로 빈다.
3. **실패가 규칙 표 점수로 적힌다** — 모델을 재는데 규칙 표를 재게 된다.
"""

from __future__ import annotations

import pytest
import requests
import responses

from clausegraph.llm import providers
from clausegraph.llm.client import LlmClient, LlmUnavailableError
from clausegraph.llm.coder import code_claim, select_options

CHAT_URL = "https://example.test/v1/chat/completions"
MODELS_URL = "https://example.test/v1/models"


def remote(**overrides: object) -> LlmClient:
    defaults: dict[str, object] = {
        "base_url": "https://example.test/v1",
        "model": "some-model",
        "api_key": "secret",
        "local": False,
    }
    return LlmClient(**(defaults | overrides))  # type: ignore[arg-type]


def reply(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


# --- 요청 모양 -------------------------------------------------------------


@responses.activate
def test_api_key_is_sent_as_bearer() -> None:
    responses.post(CHAT_URL, json=reply("K02"))

    remote().complete("sys", "user")

    assert responses.calls[0].request.headers["Authorization"] == "Bearer secret"


@responses.activate
def test_local_client_sends_no_authorization_header() -> None:
    responses.post("http://localhost:8080/v1/chat/completions", json=reply("K02"))

    LlmClient().complete("sys", "user")

    assert "Authorization" not in responses.calls[0].request.headers


@responses.activate
def test_llama_cpp_field_goes_only_to_local() -> None:
    # 원격 API는 모르는 필드를 400으로 튕긴다.
    responses.post(CHAT_URL, json=reply("K02"))
    responses.post("http://localhost:8080/v1/chat/completions", json=reply("K02"))

    remote().complete("sys", "user")
    LlmClient().complete("sys", "user")

    assert "chat_template_kwargs" not in responses.calls[0].request.body.decode()
    assert "chat_template_kwargs" in responses.calls[1].request.body.decode()


@responses.activate
def test_extra_body_is_merged_into_payload() -> None:
    responses.post(CHAT_URL, json=reply("K02"))

    remote(extra_body={"reasoning_effort": "none"}).complete("sys", "user")

    assert "reasoning_effort" in responses.calls[0].request.body.decode()


# --- 실패를 실패로 받는가 ---------------------------------------------------


@responses.activate
def test_rate_limit_is_retried() -> None:
    # 429는 무료 티어의 분당 상한이다. 한 번 맞았다고 그 건을 버리면
    # 표본이 조용히 줄어든다.
    responses.post(CHAT_URL, status=429)
    responses.post(CHAT_URL, json=reply("K02"))

    assert remote().complete("sys", "user") == "K02"
    assert len(responses.calls) == 2


@responses.activate
def test_bad_request_is_not_retried() -> None:
    # 400은 몇 번을 걸어도 같다. 무료 티어에서 헛되이 호출을 태우지 않는다.
    responses.post(CHAT_URL, status=400)

    with pytest.raises(LlmUnavailableError):
        remote().complete("sys", "user")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_error_is_retried() -> None:
    responses.post(CHAT_URL, body=requests.ConnectionError("끊겼다"))
    responses.post(CHAT_URL, json=reply("K02"))

    assert remote().complete("sys", "user") == "K02"


@responses.activate
def test_measuring_does_not_fall_back_to_rules() -> None:
    # 이걸 놓치면 원격이 죽은 동안 규칙 표의 점수가 그 모델의 점수로 적힌다.
    responses.post(CHAT_URL, status=400)

    with pytest.raises(LlmUnavailableError):
        code_claim("충치가 심해 임플란트를 했습니다", remote(), fallback=False)


@responses.activate
def test_serving_still_falls_back_to_rules() -> None:
    # 반대로 심사 중에는 멈추면 안 된다. 기본값은 예전 그대로다.
    responses.get(MODELS_URL, status=500)

    outcome = code_claim("충치가 심해 임플란트를 했습니다", remote())

    assert outcome.source == "rules"


@responses.activate
def test_selection_failure_is_not_silent_when_measuring() -> None:
    # 빈 결과로 내려가면 "아무것도 고르지 않았다"와 구별되지 않아,
    # 부정 케이스에서 점수가 부풀려진다.
    responses.post(CHAT_URL, status=400)

    with pytest.raises(LlmUnavailableError):
        select_options("치핵 수술을 받았습니다", ["가", "나"], remote(), fallback=False)


# --- 어느 단을 잴 것인가 ----------------------------------------------------


def test_only_local_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    found = providers.discover()

    assert [item.key for item in found] == ["local"]


def test_keys_add_rungs_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "m")

    found = providers.discover()

    assert [item.key for item in found] == ["local", "groq", "gemini"]


def test_blank_key_is_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # `.env`에 빈 줄로 남겨 둔 키가 단으로 잡히면, 붙지 못한 단을
    # 재느라 측정이 통째로 실패한다.
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert [item.key for item in providers.discover()] == ["local"]


def test_frontier_is_not_onprem(monkeypatch: pytest.MonkeyPatch) -> None:
    # 결론이 뒤집히는 자리다. 정확도만 보고 고르면 폐쇄망에 못 올릴 것을
    # 고르게 된다.
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "m")

    onprem = {item.key: item.onprem for item in providers.discover()}

    assert onprem == {"local": True, "groq": True, "gemini": False}
