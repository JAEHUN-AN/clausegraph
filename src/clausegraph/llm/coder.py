"""청구 서술을 질병분류 코드로 옮긴다 — 로컬 LLM이 하는 유일한 일.

notes/009에서 좁혀 둔 자리다. 면책은 코드로 못박혀 있는데(`치과치료(K00~K08)`)
청구서에는 사람 말이 적힌다("충치가 심해 임플란트를 했습니다"). `임플란트`도
`충치`도 약관에 없는 낱말이라 코드로 옮겨야 걸린다.

## 이 모듈의 가드레일

LLM에게 **판정을 맡기지 않는다.** 코드만 받고, 받은 것을 세 겹으로 거른다.

1. **모양 검사** — KCD 표기(영문 1자 + 숫자 2자, 선택적 세분류)에 맞지 않으면
   버린다. 모델이 설명을 붙이거나 "K08입니다"처럼 답해도 코드만 남는다.
2. **개수 상한** — 한 청구에서 받아들일 코드 수를 제한한다. 모델이 장황해져
   코드를 쏟아내면 면책이 과하게 걸린다.
3. **원문 보존** — 모델이 무엇을 말했는지 그대로 남긴다. 판정 근거를 되짚을 때
   "코드가 어디서 왔는가"를 답할 수 있어야 한다.

거른 뒤에도 **판정은 코드 범위 대조로 결정론적으로 된다**(agents/kcd.py).
LLM이 틀린 코드를 내놓으면 틀린 면책이 걸리지만, 없는 조항을 만들어 내거나
지급액을 흔들 수는 없다.

붙지 않으면 규칙 표(agents/terminology.py)로 내려간다 — 폐쇄망에서 서버가
안 떠 있을 때 심사가 멈추면 안 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..agents import terminology
from .client import LlmClient, LlmUnavailableError

# agents/kcd.py의 표기와 같은 모양만 받는다.
_CODE_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
_WORD_RE = re.compile(r"[A-Za-z가-힣]{2,}")
MAX_CODES = 6

SYSTEM_PROMPT = (
    "당신은 보험 청구 서술에서 한국표준질병사인분류(KCD) 코드를 뽑는 도구다.\n"
    "규칙:\n"
    "1. 코드만 쉼표로 구분해 출력한다. 설명·인사·따옴표를 붙이지 않는다.\n"
    "2. 서술에 나타난 질병·부상·시술에 해당하는 코드만 쓴다.\n"
    "3. 확실하지 않으면 그 코드를 쓰지 않는다. 추측한 코드는 잘못된 부지급으로 "
    "이어진다.\n"
    "4. 질병·부상이 언급되지 않았거나(보험료·서류·간병비 등) 해당 코드를 모르면 "
    "NONE 한 단어만 출력한다.\n"
    "출력 형식: 코드, 코드   또는   NONE"
)
# 예시에 구체적인 코드를 넣지 않는다.
#
# 처음에는 "충치로 임플란트 -> K02, K08" 같은 예시를 달았는데, 4B 모델이
# 그 코드를 **무관한 입력에 그대로 복사**했다. 요실금·치핵·고도비만·시험관
# 시술이 전부 K02, K08로 나왔다(notes/012). 형식만 보여 주고 답은 보여
# 주지 않는다.


@dataclass(frozen=True)
class CodingResult:
    """무엇을 받아 무엇을 남겼는지."""

    codes: tuple[str, ...] = ()
    raw: str = ""
    source: str = "none"
    dropped: tuple[str, ...] = ()


def parse_codes(
    raw: str, limit: int = MAX_CODES
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """응답에서 코드만 남긴다. (받아들인 것, 버린 조각)."""
    text = raw.strip()
    if not text or text.upper().startswith("NONE"):
        return (), ()

    upper = text.upper()
    accepted = tuple(dict.fromkeys(_CODE_RE.findall(upper)))
    # 코드 표기를 걷어낸 나머지 — 모델이 덧붙인 말이다.
    leftover = _CODE_RE.sub(" ", upper)
    dropped = tuple(token for token in _WORD_RE.findall(leftover) if token != "NONE")
    return accepted[:limit], dropped


def code_claim(narrative: str, client: LlmClient | None = None) -> CodingResult:
    """서술에서 코드를 뽑는다. LLM이 없으면 규칙 표로 내려간다."""
    llm = client or LlmClient.from_env()
    if not llm.available():
        return CodingResult(codes=terminology.lookup(narrative), source="rules")

    try:
        raw = llm.complete(SYSTEM_PROMPT, narrative)
    except LlmUnavailableError:
        return CodingResult(codes=terminology.lookup(narrative), source="rules")

    codes, dropped = parse_codes(raw)
    return CodingResult(codes=codes, raw=raw.strip(), source="llm", dropped=dropped)


SELECT_PROMPT = (
    "당신은 보험 청구 서술이 아래 목록의 어느 항목에 해당하는지 고르는 도구다.\n"
    "규칙:\n"
    "1. 해당하는 항목의 번호만 쉼표로 구분해 출력한다. 설명을 붙이지 않는다.\n"
    "2. 목록에 없는 번호를 쓰지 않는다.\n"
    "3. 해당하는 항목이 없으면 NONE 한 단어만 출력한다.\n"
    "출력 형식: 1, 3   또는   NONE"
)

_NUMBER_RE = re.compile(r"\b(\d{1,2})\b")


def select_options(
    narrative: str, options: list[str], client: LlmClient | None = None
) -> tuple[tuple[int, ...], str]:
    """서술이 어느 항목에 해당하는지 고르게 한다. (고른 번호, 원문).

    코드를 **생성**하게 하는 대신 주어진 것 중에서 **선택**하게 한다.
    4B 모델이 KCD 코드를 기억하지 못한다는 것이 측정으로 드러났으므로
    (notes/012), 기억을 요구하지 않는 형태로 바꿔 본다.

    번호가 목록 범위를 벗어나면 버린다 — 없는 항목을 고르는 것은 환각이다.
    """
    llm = client or LlmClient.from_env()
    listing = "\n".join(f"{index}) {label}" for index, label in enumerate(options, 1))
    try:
        raw = llm.complete(SELECT_PROMPT, f"청구 내용: {narrative}\n\n목록:\n{listing}")
    except LlmUnavailableError:
        return (), ""

    text = raw.strip()
    if not text or text.upper().startswith("NONE"):
        return (), text
    picked = tuple(
        dict.fromkeys(
            number
            for number in (int(value) for value in _NUMBER_RE.findall(text))
            if 1 <= number <= len(options)
        )
    )
    return picked, text


def make_enricher(client: LlmClient | None = None):
    """`extract_claim(enrich=...)`에 꽂을 함수를 만든다."""
    llm = client or LlmClient.from_env()

    def enrich(narrative: str) -> tuple[str, ...]:
        return code_claim(narrative, llm).codes

    return enrich
