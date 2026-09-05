# MCP 서버 붙이기

## 실행

```
uv run --extra mcp --extra graph --extra rag --extra onnx \
  python -m clausegraph.mcp_server.server
```

Neo4j와 pgvector가 떠 있어야 한다.

```
docker compose up -d
```

## Claude Desktop 등록

`claude_desktop_config.json`에 절대경로로 넣는다. stdio 서버는 클라이언트가
띄우는 하위 프로세스이므로 상대경로와 `.env` 자동 탐색이 통하지 않는다 —
환경변수를 설정에 직접 적는다.

```json
{
  "mcpServers": {
    "clausegraph": {
      "command": "C:\Users\<user>\.local\bin\uv.exe",
      "args": [
        "run", "--directory", "C:\workspace\clausegraph",
        "--extra", "mcp", "--extra", "graph", "--extra", "rag", "--extra", "onnx",
        "python", "-m", "clausegraph.mcp_server.server"
      ],
      "env": {
        "NEO4J_URI": "bolt://localhost:7688",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "clausegraph",
        "PG_DSN": "postgresql://clausegraph:clausegraph@localhost:5434/clausegraph",
        "CLAUSEGRAPH_ONNX_DIR": "C:\workspace\kograph\models\bge-m3-onnx"
      }
    }
  }
}
```

**stdio 서버는 핫 리로드가 안 된다.** 코드를 고쳐도 그 프로세스가 살아 있는
한 옛 코드로 답한다. 수정 후에는 새 대화창을 열어야 한다.

## 도구 7개

| 도구 | 언제 부르는가 |
|---|---|
| `list_products` | 어떤 상품·어느 시점을 다룰 수 있는지 모를 때 **가장 먼저** |
| `resolve_terms_version` | 조항을 인용하기 **전에**. 가입일 → 적용 약관 |
| `list_exclusions` | "이거 보상되나요"류. 면책을 **전부** 열거 |
| `check_diagnosis_codes` | 진단코드를 알 때. 코드 범위 대조로 결정론 판정 |
| `screen_exclusions` | 판정까지 가지 않고 면책만 볼 때. 확실/불확실 구분 |
| `search_clauses` | "입원의 정의가 뭔가"처럼 조항 내용을 묻는 서술형 질문 |
| `adjudicate_claim` | 청구가 자연어로 들어왔을 때. 전체 심사 |

## 설계상 지키는 것

**면책은 골라 주지 않고 열거한다.** `list_exclusions`는 그 상품·그 시점의
면책을 전부 돌려준다. 닮은 것 몇 개를 골라 주는 방식은 지급을 뒤집는 면책을
절반 놓친다(notes/008: 벡터 recall 32.4%).

**가입일이 없으면 답하지 않는다.** 같은 조문 번호가 시점에 따라 다른
내용이다. 서버 instructions에 "가입일을 모르면 먼저 물어야 한다"를 적어
모델이 되묻게 한다.

**수집 범위 밖은 거절한다.** 2020년 가입자를 물으면 판정하지 않고 범위를
알린다. 지금 수집한 약관은 2025-04 이후 4개 버전뿐이다.

**판정은 보조라고 말한다.** `adjudicate_claim` 결과 끝에 "최종 결정은
심사자가 한다"를 붙이고, 도구 설명에도 단정하지 말라고 적었다.
