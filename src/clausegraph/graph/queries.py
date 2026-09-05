"""그래프가 실제로 답할 수 있어야 하는 질문들.

이 프로젝트가 "벡터 검색으로는 구조적으로 안 된다"고 주장하는 두 가지를
그대로 쿼리로 옮겼다.
"""

from __future__ import annotations

# 시점 축 — 가입일에 적용되던 약관 버전
VERSION_AT = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
RETURN v.effective_from AS effective_from,
       v.effective_to AS effective_to,
       v.article_count AS article_count
"""

# 가입 시점 기준으로 그 상품의 면책 조문을 모두
EXCLUSIONS_AT = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
MATCH (a:Article:Exclusion)-[:IN_VERSION]->(v)
MATCH (a)-[:OF_PRODUCT]->(p:Product {name: $product})
OPTIONAL MATCH (a)-[:HAS_ITEM]->(i:Item)
RETURN a.number AS number, a.title AS title, count(i) AS item_count
ORDER BY toInteger(split(a.number, '의')[0])
"""

# 같은 조문이 시점에 따라 어떻게 달라졌나
ARTICLE_HISTORY = """
MATCH (a:Article {unit: $product, number: $number})
RETURN a.effective_from AS effective_from,
       size(a.text) AS length,
       a.revised_on AS revised_on
ORDER BY a.effective_from DESC
"""

# 두 시점 사이에 면책 조문이 바뀐 상품
EXCLUSIONS_CHANGED = """
MATCH (newer:Article:Exclusion)-[:SUPERSEDES]->(older:Article)
WHERE newer.text <> older.text
RETURN newer.unit AS product,
       newer.number AS number,
       older.effective_from AS was,
       newer.effective_from AS now,
       size(older.text) AS old_length,
       size(newer.text) AS new_length
ORDER BY product, toInteger(split(number, '의')[0])
"""

# 그래프에만 있고 한 조문에는 없는 정보 — 상품별 면책 사유(호) 개수
EXCLUSION_ITEM_COUNTS = """
MATCH (v:Version)
WHERE v.effective_to = $open_ended
MATCH (a:Article:Exclusion)-[:IN_VERSION]->(v)
MATCH (a)-[:OF_PRODUCT]->(p:Product)
OPTIONAL MATCH (a)-[:HAS_ITEM]->(i:Item)
RETURN p.name AS product, count(DISTINCT a) AS articles, count(i) AS items
ORDER BY items DESC
"""

COUNTS = """
MATCH (n)
RETURN labels(n) AS labels, count(*) AS count
ORDER BY count DESC
"""


# 상품이 존재했던 버전 — 사라진 상품을 드러낸다.
# 2025년 가입자의 '실손 특별약관'은 지금 문서에 없는 상품이다.
PRODUCT_LIFESPAN = """
MATCH (a:Article)-[:OF_PRODUCT]->(p:Product)
WHERE p.name CONTAINS $keyword
RETURN p.name AS product, collect(DISTINCT a.effective_from) AS versions
ORDER BY product
"""
