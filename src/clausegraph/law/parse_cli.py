"""수집된 표준약관을 조문 구조로 파싱한다.

    uv run python -m clausegraph.law.parse_cli

시행일자별로 파싱해 JSON으로 남기고, 상품·조문 수와 면책 조문을 요약한다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .models import TermsDocument
from .terms_parser import parse_terms

TERMS_DIRNAME = "terms"
PARSED_DIRNAME = "parsed"

# 면책 조문 — 보장을 무효화하는 조항. 이 프로젝트의 핵심 대상이다.
EXCLUSION_TITLE_MARKERS = ("보상하지 않는", "지급하지 않는", "보상하지 아니")


def is_exclusion(title: str) -> bool:
    return any(marker in title for marker in EXCLUSION_TITLE_MARKERS)


def parse_file(path: Path) -> TermsDocument:
    """파일명 `<시행일자>_<행정규칙일련번호>.txt`에서 메타데이터를 읽는다."""
    effective_on, _, seq = path.stem.partition("_")
    if not effective_on.isdigit() or not seq.isdigit():
        raise ValueError(f"파일명 형식이 다르다: {path.name}")
    return parse_terms(path.read_text(encoding="utf-8"), effective_on, int(seq))


def run(data_dir: Path) -> int:
    terms_dir = data_dir / TERMS_DIRNAME
    paths = sorted(terms_dir.glob("*.txt"), reverse=True)
    if not paths:
        print(f"파싱할 약관이 없다: {terms_dir} — law.collect를 먼저 돌릴 것", file=sys.stderr)
        return 1

    parsed_dir = data_dir / PARSED_DIRNAME
    parsed_dir.mkdir(parents=True, exist_ok=True)

    for path in paths:
        doc = parse_file(path)
        (parsed_dir / f"{path.stem}.json").write_text(
            doc.model_dump_json(indent=2), encoding="utf-8"
        )
        units = Counter(article.unit for article in doc.articles)
        exclusions = [a for a in doc.articles if is_exclusion(a.title)]
        revised = sum(1 for a in doc.articles if a.revised_on)
        print(
            f"{doc.effective_on}  조문 {len(doc.articles):4d}  상품 {len(units):2d}  "
            f"면책 {len(exclusions):2d}  개정표기 {revised:3d}"
        )

    latest = parse_file(paths[0])
    print(f"\n=== 최신본 {latest.effective_on} 상품별 조문 ===")
    for unit, count in Counter(a.unit for a in latest.articles).items():
        print(f"  {count:4d}  {unit}")

    print("\n=== 면책 조문 ===")
    for article in latest.articles:
        if not is_exclusion(article.title):
            continue
        items = sum(len(paragraph.items) for paragraph in article.paragraphs)
        note = "  ← 사유가 표 안에 있다" if items == 0 and "┃" in article.text else ""
        print(f"  {article.key:52s} 호 {items:2d}{note}")

    print(f"\n저장: {parsed_dir}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="표준약관 조문 파싱")
    parser.add_argument("--data", type=Path, default=Path("data/law"))
    args = parser.parse_args()
    return run(args.data)


if __name__ == "__main__":
    raise SystemExit(main())
