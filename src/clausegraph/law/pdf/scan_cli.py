"""스캔본으로 왔다면 어떻게 되는지 재 본다.

    uv run --extra ocr python -m clausegraph.law.pdf.scan_cli --effective-on 20260910 --pages 6

글자층이 있는 PDF를 이미지로 굽고 OCR로 되읽어, **원래 글자층을 정답 삼아**
글자 오류율과 조문 머리글 인식률을 잰다. 라벨을 만들 필요가 없다.

쪽을 고르게 뽑되 목차는 뺀다 — 점선 목차는 본문이 아니라서 오류율을 흐린다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cli import CACHE_SUFFIX, PDF_DIRNAME
from .layout import Line
from .scan import DEFAULT_DPI, PageScan, scan_pages

SKIP_LEADING_PAGES = 1


def truth_by_page(lines: list[Line]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for line in lines:
        grouped.setdefault(line.page_index, []).append(line.text)
    return grouped


def pick_pages(grouped: dict[int, list[str]], count: int) -> dict[int, list[str]]:
    """본문에서 고르게 뽑는다."""
    body = sorted(index for index in grouped if index >= SKIP_LEADING_PAGES)
    if not body or count <= 0:
        return {}
    step = max(1, len(body) // count)
    chosen = body[::step][:count]
    return {index: grouped[index] for index in chosen}


def summarize(scans: list[PageScan]) -> str:
    truth_chars = sum(len(scan.truth) for scan in scans)
    errors = sum(round(scan.cer * len(scan.truth)) for scan in scans)
    heads_truth = sum(scan.heads_truth for scan in scans)
    heads_ocr = sum(scan.heads_ocr for scan in scans)
    overall = errors / truth_chars if truth_chars else 0.0
    return "\n".join(
        [
            f"쪽 {len(scans)}개 / 글자 {truth_chars:,}자",
            f"글자 오류율(CER) {overall:.1%}",
            f"조문 머리글 {heads_ocr}/{heads_truth}개 인식",
        ]
    )


def run(data_dir: Path, effective_on: str, count: int, dpi: int) -> int:
    pdf_dir = data_dir / PDF_DIRNAME
    cache = pdf_dir / f"{effective_on}{CACHE_SUFFIX}"
    if not cache.exists():
        print(f"줄 캐시가 없다: {cache} — law.pdf.cli를 먼저 돌릴 것")
        return 1

    lines = [Line(**row) for row in json.loads(cache.read_text(encoding="utf-8"))]
    chosen = pick_pages(truth_by_page(lines), count)
    print(f"고른 쪽: {sorted(index + 1 for index in chosen)}")

    scans = scan_pages(pdf_dir / f"{effective_on}.pdf", chosen, dpi=dpi)
    for scan in scans:
        print(
            f"  {scan.page_index + 1:4}쪽  CER {scan.cer:6.1%}"
            f"  머리글 {scan.heads_ocr}/{scan.heads_truth}"
        )
    print(summarize(scans))

    out = pdf_dir / f"{effective_on}.scan.json"
    out.write_text(
        json.dumps(
            {
                "dpi": dpi,
                "pages": [
                    {
                        "page_index": scan.page_index,
                        "cer": scan.cer,
                        "truth_chars": len(scan.truth),
                        "heads_truth": scan.heads_truth,
                        "heads_ocr": scan.heads_ocr,
                    }
                    for scan in scans
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n리포트: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="스캔본 경로의 손실을 잰다")
    parser.add_argument("--data-dir", type=Path, default=Path("data/law"))
    parser.add_argument("--effective-on", required=True)
    parser.add_argument("--pages", type=int, default=6, help="표본 쪽 수")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    args = parser.parse_args(argv)
    return run(args.data_dir, args.effective_on, args.pages, args.dpi)


if __name__ == "__main__":
    raise SystemExit(main())
