"""시행일자별 표준약관 수집 CLI.

    uv run python -m clausegraph.law.collect --limit 10

현행 1건 + 연혁 183건 중 최근 것부터 받는다. 본문 XML은 한 건이 4~5MB라
캐시해 두고, 이미 받은 버전은 건너뛴다.

세칙이 개정돼도 별표15가 그대로인 경우가 많다(최근 10건 -> 실제 4개 버전).
그래서 내용 해시로 묶어 **약관이 실제로 바뀐 시점**만 버전으로 잡고,
각 버전에 적용 구간 [effective_from, effective_to)를 매긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .client import CURRENT, HISTORICAL, LawClient, MissingOcError
from .models import ADMRUL_NAME, AdmRulRef, StandardTerms
from .parser import LawApiError, extract_standard_terms, parse_admrul_list

XML_DIRNAME = "xml"
TERMS_DIRNAME = "terms"
MANIFEST_FILENAME = "manifest.json"


def list_versions(client: LawClient) -> list[AdmRulRef]:
    """현행 + 연혁을 합쳐 발령일자 내림차순으로 돌려준다."""
    refs: dict[int, AdmRulRef] = {}
    for nw in (CURRENT, HISTORICAL):
        page = 1
        while True:
            rows = parse_admrul_list(client.search_admrul(ADMRUL_NAME, nw=nw, page=page))
            rows = [ref for ref in rows if ref.name == ADMRUL_NAME]
            if not rows:
                break
            refs.update({ref.seq: ref for ref in rows})
            page += 1
    return sorted(refs.values(), key=lambda ref: ref.promulgated_on, reverse=True)


def fetch_version(client: LawClient, ref: AdmRulRef, xml_dir: Path) -> StandardTerms:
    """본문 XML을 캐시하고 표준약관을 꺼낸다."""
    xml_path = xml_dir / f"{ref.seq}.xml"
    if xml_path.exists():
        xml = xml_path.read_text(encoding="utf-8")
    else:
        xml = client.fetch_admrul(ref.seq)
        xml_path.write_text(xml, encoding="utf-8")
    return extract_standard_terms(xml)


def group_by_content(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """같은 내용의 연속 개정을 한 버전으로 묶고 적용 구간을 매긴다.

    entries는 시행일자 내림차순이다. 어떤 버전의 적용 종료일은 그보다 나중에
    시작하는 버전의 시행일자다 — 가장 최근 버전은 열려 있다(None).
    """
    ordered = sorted(entries, key=lambda e: str(e["effective_on"]))
    versions: list[dict[str, object]] = []
    for entry in ordered:
        if versions and versions[-1]["content_sha256"] == entry["content_sha256"]:
            versions[-1]["admrul_seqs"].append(entry["admrul_seq"])  # type: ignore[union-attr]
            continue
        versions.append(
            {
                "content_sha256": entry["content_sha256"],
                "effective_from": entry["effective_on"],
                "effective_to": None,
                "char_count": entry["char_count"],
                "file": entry["file"],
                "admrul_seqs": [entry["admrul_seq"]],
            }
        )
    for earlier, later in zip(versions, versions[1:], strict=False):
        earlier["effective_to"] = later["effective_from"]
    return list(reversed(versions))


def run(out_dir: Path, limit: int | None, drop_xml: bool = False) -> int:
    client = LawClient.from_env()
    xml_dir = out_dir / XML_DIRNAME
    terms_dir = out_dir / TERMS_DIRNAME
    xml_dir.mkdir(parents=True, exist_ok=True)
    terms_dir.mkdir(parents=True, exist_ok=True)

    versions = list_versions(client)
    print(f"{ADMRUL_NAME}: 현행+연혁 {len(versions)}건")
    targets = versions[:limit] if limit else versions
    print(f"수집 대상 {len(targets)}건 (발령일자 최신순)\n")

    entries: list[dict[str, object]] = []
    failures: list[tuple[int, str]] = []
    for ref in targets:
        try:
            terms = fetch_version(client, ref, xml_dir)
        except LawApiError as exc:
            # 오래된 연혁에는 별표15가 없을 수 있다 — 기록하고 계속한다.
            failures.append((ref.seq, str(exc)))
            print(f"  건너뜀 {ref.promulgated_on} seq={ref.seq}: {exc}", file=sys.stderr)
            continue

        path = terms_dir / f"{terms.effective_on}_{terms.admrul_seq}.txt"
        path.write_text(terms.text, encoding="utf-8")
        entries.append(
            {
                "admrul_seq": terms.admrul_seq,
                "effective_on": terms.effective_on,
                "promulgated_on": terms.promulgated_on,
                "status": ref.status,
                "title": terms.title,
                "char_count": terms.char_count,
                "content_sha256": hashlib.sha256(terms.text.encode("utf-8")).hexdigest(),
                "file": path.name,
            }
        )
        print(
            f"  {terms.effective_on}  seq={terms.admrul_seq}  "
            f"{terms.char_count:,}자  {ref.status}",
            flush=True,
        )

    versions = group_by_content(entries)
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {"admrul_name": ADMRUL_NAME, "revisions": entries, "versions": versions},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"\n세칙 개정 {len(entries)}건 -> 표준약관 {len(versions)}개 버전")
    for version in versions:
        end_label = version["effective_to"] or "현재"
        revision_count = len(version["admrul_seqs"])
        print(
            f"  {version['effective_from']} ~ {end_label}  "
            f"{version['char_count']:>9,}자  (개정 {revision_count}건)"
        )

    if drop_xml:
        for cached in xml_dir.glob("*.xml"):
            cached.unlink()
        print("본문 XML 캐시를 지웠다")

    print(f"\n수집 {len(entries)}건, 실패 {len(failures)}건")
    print(f"약관 텍스트: {terms_dir}")
    print(f"목록: {out_dir / MANIFEST_FILENAME}")
    return 1 if failures and not entries else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="시행일자별 보험 표준약관 수집")
    parser.add_argument("--out", type=Path, default=Path("data/law"))
    parser.add_argument("--limit", type=int, default=10, help="최신 몇 건까지 받을지")
    parser.add_argument(
        "--drop-xml",
        action="store_true",
        help="약관을 꺼낸 뒤 본문 XML 캐시를 지운다 (한 건에 4~5MB)",
    )
    args = parser.parse_args()
    try:
        return run(args.out, args.limit, drop_xml=args.drop_xml)
    except MissingOcError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
