# 로컬 LLM 서빙 (폐쇄망)

외부 API를 부르지 않는다. GPU가 없어 llama.cpp를 CPU에서 돌린다.

## 준비

llama.cpp는 winget으로 이미 설치돼 있다.

```
winget install ggml.llamacpp
```

모델은 리포에 담지 않는다(`models/`는 git 제외). Apache-2.0인
Qwen3-4B GGUF를 쓴다.

```
curl -L -o models/Qwen3-4B-Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf
```

2.38GB.

## 띄우기

```
llama-server -m models/Qwen3-4B-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8090 --ctx-size 2048 --threads 8 --jinja
```

**포트 8090을 쓴다.** 8080은 이 워크스페이스의 다른 프로젝트(Spring)가
점유하고 있다. `.env`의 `LLM_BASE_URL`이 여기를 가리킨다.

`--jinja`는 모델의 채팅 템플릿을 쓰게 한다. Qwen3는 기본이 사고 모드라
코드 몇 개 뽑는 일에 수십 초를 쓰므로, 클라이언트가
`chat_template_kwargs={"enable_thinking": false}`로 끈다.

## 확인

```
curl http://127.0.0.1:8090/v1/models
uv run --extra graph python -m clausegraph.llm.evaluate
```

## 이 모델이 하는 일과 하지 않는 일

**하는 일** — 청구 서술을 질병분류 코드로 옮기는 것 하나뿐이다.

**하지 않는 일** — 판정, 금액 계산, 조항 인용. 판정은 코드 범위 대조로
결정론적으로 되고(`agents/kcd.py`), 금액은 코드가 계산한다.

측정 결과 **기본 경로에는 넣지 않았다.** 이유는 notes/012.

## 서버가 없으면

`coder.code_claim`이 규칙 표(`agents/terminology.py`)로 내려간다. 폐쇄망에서
서버가 안 떠 있을 때 심사가 멈추면 안 된다.
