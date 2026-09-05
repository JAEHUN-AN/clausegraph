"""1. 사실추출 — 청구 서술에서 판정에 쓸 사실을 뽑는다.

여기가 LLM이 들어갈 자리다. 지금은 규칙으로 채운다. KCD 코드·일자·입원일수는
표기가 정해져 있어 정규식이 더 정확하고, 무엇보다 **환각이 없다.** 판정의
입력이 되는 값이라 틀리면 뒤가 전부 틀어진다.

자유 서술에서 상병을 추론하는 일(예: "임플란트" -> K08)은 규칙으로 감당이
안 된다. 그 자리를 `enrich` 훅으로 비워 둔다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from .models import Claim

# KCD-8 코드 표기: 영문 1자 + 숫자 2자 (+ 소수점 세분류)
_KCD_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
_DAYS_RE = re.compile(r"(\d{1,3})\s*일\s*(?:간\s*)?입원")
_AMOUNT_RE = re.compile(r"([\d,]{4,})\s*원")
_DATE_RE = re.compile(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")

Enricher = Callable[[str], tuple[str, ...]]


def extract_claim(
    claim_id: str,
    product: str,
    enrolled_on: date,
    narrative: str,
    *,
    enrich: Enricher | None = None,
) -> Claim:
    """서술에서 사실을 뽑아 Claim을 만든다."""
    codes = tuple(dict.fromkeys(_KCD_RE.findall(narrative)))
    if enrich is not None:
        codes = tuple(dict.fromkeys([*codes, *enrich(narrative)]))

    return Claim(
        claim_id=claim_id,
        product=product,
        enrolled_on=enrolled_on,
        incident_on=_first_date(narrative),
        diagnosis_codes=codes,
        procedure=_procedure(narrative),
        hospital_days=_hospital_days(narrative),
        claimed_amount=_amount(narrative),
        narrative=narrative.strip(),
    )


def _first_date(text: str) -> date | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        # 오탈자를 조용히 넘기지 않는다 — 사고일은 판정에 직접 쓰인다.
        return None


def _hospital_days(text: str) -> int:
    match = _DAYS_RE.search(text)
    return int(match.group(1)) if match else 0


def _amount(text: str) -> int:
    match = _AMOUNT_RE.search(text)
    return int(match.group(1).replace(",", "")) if match else 0


def _procedure(text: str) -> str | None:
    for marker in ("수술", "시술", "치료", "검사"):
        match = re.search(rf"([가-힣A-Za-z0-9()·\s]{{2,20}}{marker})", text)
        if match:
            return match.group(1).strip()
    return None
