"""분쟁조정사례 수집 CLI.

    uv run python -m clausegraph.goldset.collect --out data/goldset --rgnl B

사례 하나당 JSON 하나를 쓰고, 이미 있는 파일은 건너뛴다 — 중단하고 다시
돌려도 이어서 받는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .client import DEFAULT_DELAY_SEC, FssClient
from .models import RGNL_INSURANCE, CaseRef, DisputeCase
from .parser import extract_body, html_to_text, parse_list, parse_total, split_sections

INDEX_FILENAME = "index.jsonl"
CASES_DIRNAME = "cases"


def collect_refs(client: FssClient, rgnl_code: str | None) -> list[CaseRef]:
    """목록을 끝까지 훑어 CaseRef를 모은다 (caseSlno 기준 중복 제거)."""
    first_page = client.fetch_list(page=1, rgnl_code=rgnl_code)
    total, pages = parse_total(first_page)
    print(f"목록: 전체 {total}건 / {pages}페이지 (rgnlCode={rgnl_code or '전체'})")

    by_slno: dict[int, CaseRef] = {ref.case_slno: ref for ref in parse_list(first_page)}
    for page in range(2, pages + 1):
        page_html = client.fetch_list(page=page, rgnl_code=rgnl_code)
        for ref in parse_list(page_html):
            by_slno.setdefault(ref.case_slno, ref)
        print(f"  page {page}/{pages} — 누적 {len(by_slno)}건", flush=True)

    refs = sorted(by_slno.values(), key=lambda ref: ref.case_slno)
    if len(refs) != total:
        print(f"  주의: 목록 표기 {total}건 vs 수집 {len(refs)}건 — 차이를 확인할 것")
    return refs


def fetch_case(client: FssClient, ref: CaseRef) -> DisputeCase:
    """상세 본문을 받아 섹션까지 분해한다."""
    body_text = html_to_text(extract_body(client.fetch_view(ref.case_slno)))
    return DisputeCase(ref=ref, sections=split_sections(body_text), body_text=body_text)


def run(out_dir: Path, rgnl_code: str | None, delay_sec: float, limit: int | None) -> int:
    client = FssClient(delay_sec=delay_sec)
    cases_dir = out_dir / CASES_DIRNAME
    cases_dir.mkdir(parents=True, exist_ok=True)

    refs = collect_refs(client, rgnl_code)
    (out_dir / INDEX_FILENAME).write_text(
        "\n".join(ref.model_dump_json() for ref in refs) + "\n", encoding="utf-8"
    )

    targets = refs[:limit] if limit else refs
    section_names: Counter[str] = Counter()
    fetched = skipped = 0
    failures: list[tuple[int, str]] = []

    for ref in targets:
        case_path = cases_dir / f"{ref.case_slno}.json"
        if case_path.exists():
            skipped += 1
            section_names.update(json.loads(case_path.read_text(encoding="utf-8"))["sections"].keys())
            continue
        try:
            case = fetch_case(client, ref)
        except Exception as exc:  # 개별 실패는 기록하고 계속 — 전체를 멈추지 않는다.
            failures.append((ref.case_slno, f"{type(exc).__name__}: {exc}"))
            print(f"  실패 caseSlno={ref.case_slno}: {exc}", file=sys.stderr)
            continue
        case_path.write_text(case.model_dump_json(indent=2), encoding="utf-8")
        section_names.update(case.sections.keys())
        fetched += 1
        print(f"  받음 {ref.case_slno} — {ref.cvpl} | {ref.title[:40]}", flush=True)

    print(f"\n수집 완료: 신규 {fetched}건, 건너뜀 {skipped}건, 실패 {len(failures)}건")
    print(f"저장 위치: {cases_dir}")
    print("\n섹션 마커 분포:")
    for name, count in section_names.most_common(15):
        print(f"  {count:4d}  {name}")
    if failures:
        print("\n실패 목록:")
        for slno, reason in failures:
            print(f"  {slno}: {reason}")
    return 1 if failures else 0


def _force_utf8_output() -> None:
    """Windows 콘솔 기본 인코딩(cp949)은 한글·기호 출력에서 깨진다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="금감원 분쟁조정사례 수집")
    parser.add_argument("--out", type=Path, default=Path("data/goldset"))
    parser.add_argument(
        "--rgnl",
        default=RGNL_INSURANCE,
        help="권역 코드 (B=보험, A=은행ㆍ중소서민, C=금융투자, 빈 값=전체)",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC)
    parser.add_argument("--limit", type=int, default=None, help="상세 수집 건수 제한 (시험용)")
    args = parser.parse_args()
    return run(args.out, args.rgnl or None, args.delay, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
