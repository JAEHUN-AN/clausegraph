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

from .models import Claim, ClaimHistory

# KCD-8 코드 표기: 영문 1자 + 숫자 2자 (+ 소수점 세분류)
_KCD_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
_DAYS_RE = re.compile(r"(\d{1,3})\s*일\s*(?:간\s*)?입원")
_DATE_RE = re.compile(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")

# 사람은 "3,000,000원"이라고 쓰지 않는다. "300만원"이라고 쓴다.
#
# 처음에는 `[\d,]{4,}\s*원` 하나였다. 기계가 적은 금액만 읽는 정규식이라
# 실제 분쟁 문장 160건에서 **한 번도 발화하지 않았고**, 후속 질문에서는
# 더 나빴다 — "작년에 300만원 받았는데 왜 부지급이죠"가 새 사실로 잡히지
# 않아 낡은 판정을 그대로 설명할 뻔했다(notes/033).
_UNITS = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000}
_AMOUNT_RE = re.compile(r"([\d,]+)\s*(억|천만|백만|만)?\s*원")
# 이보다 작은 값은 금액으로 세지 않는다 — 조문 번호나 횟수를 금액으로
# 읽는 것을 막는다.
MIN_AMOUNT = 1_000


def parse_amounts(text: str) -> tuple[int, ...]:
    """글에 적힌 금액을 전부 원 단위로 읽는다. 나온 순서를 지킨다.

    `삼백만원`처럼 **한글 수사는 읽지 못한다.** 숫자로 적힌 것만 본다.
    """
    found: list[int] = []
    for digits, unit in _AMOUNT_RE.findall(text):
        cleaned = digits.replace(",", "")
        if not cleaned:
            continue
        value = int(cleaned) * _UNITS.get(unit, 1)
        if value >= MIN_AMOUNT:
            found.append(value)
    return tuple(found)

Enricher = Callable[[str], tuple[str, ...]]

# 구조화 입력의 진단코드 한 개. 점 있는 표기(F20.0)와 상병마스터의 점 없는
# 표기(F200) 둘 다 받는다. 세분류는 두 자리까지만 남긴다 — 면책 범위 대조는
# 세 자리 분류로 하고, 대조기(`agents.kcd`)가 두 자리까지만 읽는다.
_CODE_TOKEN_RE = re.compile(r"([A-Z])(\d{2})(?:\.?(\d{1,4}))?")
# 구분자는 사람과 기계가 쓰는 것을 다 받는다 — 쉼표, 공백, 세미콜론, 전각 쉼표,
# 가운뎃점, JSON 배열의 대괄호·따옴표.
_CODE_SPLIT_RE = re.compile(r"[\s,;，、·/|\[\]\"']+")


class InvalidDiagnosisCodeError(ValueError):
    """구조화 입력에 진단코드가 아닌 값이 섞였다.

    조용히 버리면 안 된다. 버린 토큰이 면책 코드였다면 그 면책을 건너뛰고
    지급하게 된다 — `"S62.0 F20"`을 한 덩어리로 읽어 F20(조현병) 면책을 놓친
    것이 그 모양이었다(검토 HIGH-1). 판정하지 않고 되돌려 보낸다.
    """


def parse_codes(raw: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """구조화 입력의 진단코드를 `X00` 또는 `X00.d(d)` 꼴로 고른다.

    전부 빈 토큰이면 빈 튜플 — "주지 않았다"로 읽는다. 하나라도 코드가 아니면
    `InvalidDiagnosisCodeError`.
    """
    tokens = [
        token
        for item in raw
        for token in _CODE_SPLIT_RE.split(str(item).strip().upper())
        if token
    ]
    codes: list[str] = []
    invalid: list[str] = []
    for token in tokens:
        match = _CODE_TOKEN_RE.fullmatch(token)
        if match is None:
            invalid.append(token)
            continue
        letter, category, subdivision = match.groups()
        codes.append(
            f"{letter}{category}.{subdivision[:2]}" if subdivision else f"{letter}{category}"
        )
    if invalid:
        raise InvalidDiagnosisCodeError(
            f"진단코드가 아닌 값: {', '.join(invalid)} — KCD 코드(예: S62.0, F200)만 넣을 것"
        )
    return tuple(dict.fromkeys(codes))


def extract_claim(
    claim_id: str,
    product: str,
    enrolled_on: date,
    narrative: str,
    *,
    enrich: Enricher | None = None,
    history: ClaimHistory | None = None,
    room_charge: int = 0,
    diagnosis_codes: tuple[str, ...] = (),
    claimed_amount: int | None = None,
    sum_insured: int | None = None,
    treated_abroad: bool | None = None,
) -> Claim:
    """서술에서 사실을 뽑아 Claim을 만든다.

    `history`는 서술에서 뽑지 않는다 — 올해 기지급액과 통원 횟수는 청구인이
    적는 것이 아니라 **보험사 시스템이 아는 값**이다. 없으면 없는 대로 두고,
    계산기가 지급액 대신 상한을 말한다(notes/027).

    **구조화 입력이 서술보다 앞선다.** 진단서·영수증을 읽는 앞단이 코드와
    금액을 이미 뽑아 왔으면 서술에서 다시 추정하지 않는다.

    - `diagnosis_codes`를 주면 `enrich`를 부르지 않는다. enrich는 상병명
      부분일치라 서술의 '오른쪽'에서 C34를, '검사비만'에서 E66을 만든다 —
      진단서에 코드가 있는데 그 추정을 섞으면 서류에 없는 면책 코드로 부지급이
      날 수 있다. **서술에 적힌 코드는 버리지 않는다.** 그건 추정이 아니라
      정규식으로 읽은 값이고, 버리면 서술의 "기왕 진단 F20"이 면책에서 빠진다.
    - 진단코드는 엄격하게 읽는다(`parse_codes`). 코드가 아닌 토큰이 섞이면
      판정하지 않고 `InvalidDiagnosisCodeError`를 낸다.
    - `claimed_amount`를 주면 서술의 금액을 읽지 않는다. 서술은 원 단위만
      읽으므로 "USD 1,800"은 0원이 된다. 환산은 앞단의 일이다.
    - 0 이하의 `claimed_amount`·`sum_insured`는 "주지 않았다"로 읽는다.
      가입금액 0원 계약은 없다 — OCR의 빈칸이 0으로 들어온 것일 가능성이 크고,
      그대로 쓰면 사람에게 가야 할 건이 "부분지급 0원"으로 확정된다.
    """
    stated = parse_codes(diagnosis_codes) if diagnosis_codes else ()
    written = tuple(_KCD_RE.findall(narrative))
    if stated:
        codes = tuple(dict.fromkeys([*stated, *written]))
    else:
        codes = tuple(dict.fromkeys(written))
        if enrich is not None:
            codes = tuple(dict.fromkeys([*codes, *enrich(narrative)]))

    stated_amount = claimed_amount if claimed_amount is not None and claimed_amount > 0 else None
    return Claim(
        claim_id=claim_id,
        history=history,
        room_charge=room_charge,
        product=product,
        enrolled_on=enrolled_on,
        incident_on=_first_date(narrative),
        diagnosis_codes=codes,
        procedure=_procedure(narrative),
        hospital_days=_hospital_days(narrative),
        claimed_amount=_amount(narrative) if stated_amount is None else stated_amount,
        sum_insured=sum_insured if sum_insured is not None and sum_insured > 0 else None,
        treated_abroad=treated_abroad,
        stated_codes=stated,
        stated_amount=stated_amount,
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
    amounts = parse_amounts(text)
    return amounts[0] if amounts else 0


def _procedure(text: str) -> str | None:
    for marker in ("수술", "시술", "치료", "검사"):
        match = re.search(rf"([가-힣A-Za-z0-9()·\s]{{2,20}}{marker})", text)
        if match:
            return match.group(1).strip()
    return None
