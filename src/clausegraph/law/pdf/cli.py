"""PDF 조판본을 파싱해 XML 경로와 맞대고 채점한다.

    uv run python -m clausegraph.law.pdf.cli --effective-on 20260910

`data/law/pdf/<시행일자>.pdf`를 읽어 `data/law/parsed/<시행일자>_<seq>.json`과
비교한다. 줄 추출은 492쪽에 수십 초가 걸리므로 결과를 캐시에 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..exclusion_table import parse_exclusion_table
from ..models import TermsDocument
from ..table_parser import Lexicon
from .compare import Report, breakdown, classify, compare, first_difference
from .exclusions import (
    article_spans,
    collect_pdf_exclusions,
    compare_exclusions,
    is_exclusion,
    page_index_of,
)
from .layout import Line, extract_lines
from .reconstruct import locate_pages, parse_pdf_terms

PDF_DIRNAME = "pdf"
PARSED_DIRNAME = "parsed"
TERMS_DIRNAME = "terms"
CACHE_SUFFIX = ".lines.json"
MAX_SHOWN = 10


def load_lines(
    pdf_path: Path, cache_path: Path, *, detect_tables: bool, refresh: bool
) -> list[Line]:
    if cache_path.exists() and not refresh:
        rows = json.loads(cache_path.read_text(encoding="utf-8"))
        return [Line(**row) for row in rows]

    lines = extract_lines(pdf_path, detect_tables=detect_tables)
    cache_path.write_text(
        json.dumps([asdict(line) for line in lines], ensure_ascii=False), encoding="utf-8"
    )
    return lines


def load_xml_document(data_dir: Path, effective_on: str) -> TermsDocument:
    matches = sorted((data_dir / PARSED_DIRNAME).glob(f"{effective_on}_*.json"))
    if not matches:
        raise FileNotFoundError(
            f"{effective_on} 판본의 XML 파싱 결과가 없다 — 먼저 law.parse_cli를 돌릴 것"
        )
    return TermsDocument.model_validate_json(matches[-1].read_text(encoding="utf-8"))


def summarize(report: Report, pdf_total: int) -> str:
    lines = [
        f"XML 조문 {report.xml_total}개 / PDF 조문 {pdf_total}개",
        f"조문 일치 {len(report.shared)}개 (재현율 {report.recall:.1%})",
        f"본문 일치 {len(report.shared) - len(report.body_mismatches)}개"
        f" ({report.body_agreement:.1%})",
        f"제목 불일치 {len(report.title_mismatches)}개 /"
        f" 계층 불일치 {len(report.depth_mismatches)}개",
        f"쪽 번호 확보 {report.pages_found}/{len(report.shared)}개",
        f"깨진 항 번호 되살림 {report.recovered_confirmed}/{report.recovered_total}개 확인",
    ]
    if report.xml_only:
        shown = ", ".join(report.xml_only[:MAX_SHOWN])
        lines.append(f"XML에만 있는 조문 {len(report.xml_only)}개: {shown}")
    if report.pdf_only:
        shown = ", ".join(report.pdf_only[:MAX_SHOWN])
        lines.append(f"PDF에만 있는 조문 {len(report.pdf_only)}개: {shown}")
    return "\n".join(lines)


def run_exclusions(
    data_dir: Path, pdf_path: Path, xml_doc: TermsDocument, pages: dict[str, int], lines: list[Line]
) -> dict[str, object]:
    """표 안의 면책 사유를 두 경로로 뽑아 맞댄다."""
    lexicon = Lexicon.from_terms_dir(data_dir / TERMS_DIRNAME)
    spans = article_spans(xml_doc, pages)
    printed_to_index = page_index_of(lines)

    print("\n-- 표 안의 면책 사유 --")
    rows: list[dict[str, object]] = []
    for article in xml_doc.articles:
        if not is_exclusion(article.title) or article.key not in spans:
            continue
        xml_side = parse_exclusion_table(article, lexicon)
        if not xml_side:
            continue
        pdf_side = collect_pdf_exclusions(pdf_path, lexicon, spans[article.key], printed_to_index)
        report = compare_exclusions(xml_side, pdf_side)
        print(
            f"  {article.unit[:28]:30} 사유 {report.xml_total:3}개"
            f" | 일치 {report.matched:3} ({report.recall:.1%})"
            f" | PDF에만 {len(report.pdf_only)}"
        )
        rows.append(
            {
                "key": article.key,
                "xml_total": report.xml_total,
                "matched": report.matched,
                "recall": report.recall,
                "xml_only": list(report.xml_only[:MAX_SHOWN]),
                "pdf_only": list(report.pdf_only[:MAX_SHOWN]),
            }
        )

    total = sum(int(row["xml_total"]) for row in rows)
    matched = sum(int(row["matched"]) for row in rows)
    print(f"  {'합계':30} 사유 {total:3}개 | 일치 {matched:3} ({matched / total:.1%})")
    return {"articles": rows, "total": total, "matched": matched}


def run(
    data_dir: Path, effective_on: str, *, detect_tables: bool, refresh: bool, exclusions: bool
) -> int:
    pdf_dir = data_dir / PDF_DIRNAME
    pdf_path = pdf_dir / f"{effective_on}.pdf"
    if not pdf_path.exists():
        print(f"PDF가 없다: {pdf_path} — law.pdf.acquire를 먼저 돌릴 것", file=sys.stderr)
        return 1

    xml_doc = load_xml_document(data_dir, effective_on)
    lines = load_lines(
        pdf_path,
        pdf_dir / f"{effective_on}{CACHE_SUFFIX}",
        detect_tables=detect_tables,
        refresh=refresh,
    )
    print(f"줄 {len(lines)}개 / 표에 걸친 줄 {sum(1 for line in lines if line.in_table)}개")

    pdf_doc, built = parse_pdf_terms(lines, effective_on, xml_doc.admrul_seq)
    pages = locate_pages(pdf_doc, built)
    report = compare(xml_doc, pdf_doc, pages)

    print(summarize(report, len(pdf_doc.articles)))
    if report.body_mismatches:
        print("\n-- 본문 불일치의 원인 --")
        for kind, count in breakdown(report):
            print(f"  {kind:14} {count:4}건")
        print("\n-- 갈라지는 지점 --")
        for mismatch in report.body_mismatches[:MAX_SHOWN]:
            print(f"  {mismatch.key}: {first_difference(mismatch.xml_value, mismatch.pdf_value)}")

    exclusion_result: dict[str, object] | None = None
    if exclusions:
        exclusion_result = run_exclusions(data_dir, pdf_path, xml_doc, pages, lines)

    out = pdf_dir / f"{effective_on}.report.json"
    out.write_text(
        json.dumps(
            {
                "effective_on": effective_on,
                "xml_total": report.xml_total,
                "pdf_total": len(pdf_doc.articles),
                "shared": len(report.shared),
                "recall": report.recall,
                "body_agreement": report.body_agreement,
                "pages_found": report.pages_found,
                "recovered_total": report.recovered_total,
                "recovered_confirmed": report.recovered_confirmed,
                "xml_only": list(report.xml_only),
                "pdf_only": list(report.pdf_only),
                "title_mismatches": [asdict(m) for m in report.title_mismatches],
                "depth_mismatches": [asdict(m) for m in report.depth_mismatches],
                "body_mismatches": [
                    {"key": m.key, "cause": classify(m)} for m in report.body_mismatches
                ],
                "causes": dict(breakdown(report)),
                "pages": pages,
                "exclusions": exclusion_result,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n리포트: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="표준약관 PDF를 XML 경로와 맞대고 채점한다")
    parser.add_argument("--data-dir", type=Path, default=Path("data/law"))
    parser.add_argument("--effective-on", required=True, help="시행일자 YYYYMMDD")
    parser.add_argument("--no-tables", action="store_true", help="표 영역 탐지를 건너뛴다(빠름)")
    parser.add_argument("--refresh", action="store_true", help="줄 캐시를 다시 만든다")
    parser.add_argument(
        "--exclusions", action="store_true", help="표 안의 면책 사유까지 맞댄다(느리다)"
    )
    args = parser.parse_args(argv)
    return run(
        args.data_dir,
        args.effective_on,
        detect_tables=not args.no_tables,
        refresh=args.refresh,
        exclusions=args.exclusions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
