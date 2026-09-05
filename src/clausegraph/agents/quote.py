r"""근거 인용문을 만든다.

**인용문이 이 시스템의 산출물이다.** 판정은 한 낱말이고, 사람이 실제로
읽고 검증하는 것은 인용된 약관 문장이다. 그래서 자르는 방식이 곧 품질이다.

두 번 데였다.

1. 앞머리를 `^.*?제\d+조\([^)]*\)\s*`로 떼려다 본문 안의 조문 참조를
   제목으로 오인해, 면책 항목 1,446개 중 116개(8.0%)의 주어를 지웠다.
   최악은 11자 `"에 따라 보상합니다."` — 부지급 근거로 "보상합니다"를
   인용했다(notes/020).
2. 그냥 앞에서 N자를 자르니 **표 테두리가 인용 예산을 먹었다.** 보장 조문
   88개 중 40개(45.5%)의 인용문에 `┏━━━━┳━━━`가 들어갔고, 심한 것은
   127자 중 59자가 테두리였다(notes/021).

그래서 여기서 하는 일은 둘이다. **앞머리는 건드리지 않고**, 표가 시작되면
거기서 멈춘다.
"""

from __future__ import annotations

import re

# 표 테두리·괘선. 조문 본문의 표는 이 문자들로 그려져 있다.
_BOX_RUN_RE = re.compile(r"[─-╿▀-▟]{2,}")

# 표가 시작되기 전 산문이 이보다 짧으면, 잘라 봐야 알맹이가 없다.
# 그때는 표가 있다는 사실을 말해 주는 것이 테두리를 보여 주는 것보다 낫다.
MIN_PROSE_CHARS = 24

TABLE_MARKER = " …(표)"


def prose_quote(text: str, limit: int) -> str:
    """산문만 남긴 인용문.

    표가 시작되는 지점에서 끊는다. 표를 지우고 이어 붙이지 않는다 — 셀
    내용이 뒤엉켜 읽을 수 없는 문장이 되고, 읽을 수 없는 인용문은 근거가
    아니다. 대신 표가 있다는 것을 표시한다.
    """
    flat = " ".join(text.split())
    if not flat:
        return ""

    table = _BOX_RUN_RE.search(flat)
    prose = flat[: table.start()].strip() if table else flat

    # 표 앞에 알맹이가 없으면(표가 곧 내용인 조문) 있는 산문을 그대로 두고
    # 표시만 붙인다. 없는 문장을 만들어 내지는 않는다.
    if table is not None and len(prose) < MIN_PROSE_CHARS:
        return (prose + TABLE_MARKER).strip()[:limit]

    if len(prose) > limit:
        return prose[:limit].rstrip() + "…"
    return prose + (TABLE_MARKER if table is not None else "")
