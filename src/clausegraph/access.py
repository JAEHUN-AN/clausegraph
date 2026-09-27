"""권한 기반 데이터 접근 제어 — 누가 어느 약관을 볼 수 있는가.

notes/037에서 이 항목은 **없음**이었다. 도구 10개가 무인증이고, 어느
도구든 전 상품·전 판본을 돌려줬다.

## 축을 상품 하나로 잡은 이유

지급심사에서 권한이 실제로 갈리는 축은 상품이다. 실손 심사자는 실손
계열을 보고, 자동차 손사는 자동차보험을 본다. 제휴 채널은 자기가 파는
상품 하나만 본다.

**판본을 축으로 잡지 않았다.** "2025년 약관은 보되 2026년은 보지 말라"는
권한은 업무에 없다. 판본은 계약이 정하는 것이지 사람이 정하는 것이 아니다.

**보장종목까지 내려가지 않았다.** 축을 하나 더 내리면 조합이 상품 16개
× 보장종목으로 늘어 전수 측정이 커지는데, 그 축에서 권한이 갈리는 업무를
찾지 못했다. 필요해지면 `Principal`에 필드를 더한다.

## 게이트를 어디에 거는가

notes/023이 이 프로젝트에서 가장 비싼 교훈이었다.

> 감싸는 줄 알았던 것이 두 번째 구현이었다.

MCP 도구가 심사 경로를 감싸는 줄 알았는데 버전 선택을 자기가 다시 하고
있었고, 가입일×상품 1,472쌍 중 32쌍이 어긋났다. **권한은 경로가 셋이라
(그래프·벡터·MCP) 같은 함정이 그대로 재현될 자리다.**

그래서 게이트를 **데이터가 나오는 질의 자체**에 건다. 호출부에 걸면
호출부를 하나 빠뜨리는 순간 새고, 빠뜨렸다는 것이 조용하다.

이 모듈은 그 판정만 하고 질의를 만들지 않는다 — 판정과 질의를 한 곳에
두면 Neo4j용과 Postgres용이 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass


class AccessDeniedError(PermissionError):
    """스코프 밖 상품을 이름으로 요구했다.

    **비어 있는 결과로 돌려주지 않는다.** "그 상품에 조항이 없다"와
    "그 상품을 볼 수 없다"는 다른 말이고, 앞의 말로 뭉개면 심사자가
    없는 약관을 찾아 헤맨다. 거절은 거절이라고 말한다.
    """


@dataclass(frozen=True)
class Principal:
    """조회하는 주체와 그가 볼 수 있는 상품.

    `products`가 비어 있으면 **아무것도 못 본다.** 빈 집합을 "전체"로
    읽는 기본값은 조용한 권한 상승이다 — notes/027에서 `0`을 "모른다"가
    아니라 "받은 것이 없다"로 읽어 과다지급을 낸 것과 같은 모양이다.
    전체를 주려면 `everything()`으로 명시해야 한다.
    """

    name: str
    products: frozenset[str] = frozenset()
    unrestricted: bool = False

    @classmethod
    def everything(cls, name: str = "system") -> Principal:
        """제한 없는 주체. 적재·평가처럼 전수를 봐야 하는 자리에만 쓴다."""
        return cls(name=name, unrestricted=True)

    def can_see(self, product: str) -> bool:
        return self.unrestricted or product in self.products

    def allowed(self, universe: frozenset[str]) -> frozenset[str]:
        """이 주체가 볼 수 있는 상품을 실제 목록과 교차해 돌려준다.

        질의에 넣을 값이다. 스코프에 오타가 있어도 여기서 걸러지므로
        없는 상품 이름이 Cypher 파라미터로 흘러가지 않는다.
        """
        return universe if self.unrestricted else frozenset(self.products) & universe

    def require(self, product: str) -> None:
        """이름으로 지목한 상품을 볼 수 있는지. 못 보면 거절한다."""
        if not self.can_see(product):
            raise AccessDeniedError(
                f"{self.name}은(는) '{product}' 약관을 조회할 권한이 없다"
            )


def visible(principal: Principal, rows: list, *, product_of) -> list:
    """돌려줄 행에서 스코프 밖을 걷어낸다.

    이름으로 지목하지 않고 **검색으로 걸려 나온** 경로가 여기 온다.
    벡터 검색은 어느 상품이 나올지 호출부가 미리 모르므로 거절할 것이
    없다 — 안 보이는 것은 없는 것으로 둔다. 지목한 것은 `require`가
    거절하고, 걸려 나온 것은 여기서 조용히 빠진다. 이 둘을 섞으면
    "검색했는데 권한 오류"가 뜬다.
    """
    if principal.unrestricted:
        return rows
    return [row for row in rows if principal.can_see(product_of(row))]
