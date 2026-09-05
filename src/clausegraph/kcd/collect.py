"""심평원 상병마스터 수집 — KCD 코드표.

    uv run python -m clausegraph.kcd.collect

출처: 공공데이터포털 「건강보험심사평가원_상병마스터」
      https://www.data.go.kr/data/15067467/fileData.do
라이선스: 공공저작물 제1유형(출처표시). 파일은 리포에 담지 않는다.

## 왜 이걸 받는가

notes/012에서 로컬 LLM이 `치핵 -> G00.1`(뇌수막염), `발목 골절 -> L84.0`
(피부)처럼 **형식은 맞고 챕터가 틀린** 코드를 지어냈다. 모양 검사 가드레일은
표기만 보므로 이런 코드가 통과한다. 막으려면 "이 코드가 실제로 존재하는가"와
"그 코드가 무슨 병인가"를 물을 수 있어야 하고, 그러려면 코드표가 필요하다.

## 다운로드 경로가 단순하지 않다

data.go.kr의 파일 다운로드는 두 단계다.

1. `POST /tcs/dss/selectFileDataDownload.do` — 메타데이터(JSON). 여기에 진짜
   `atchFileId`가 들어 있다. GET으로 부르면 404다.
2. `GET /cmm/cmm/fileDownload.do?atchFileId=...` — 파일.

상세 페이지 HTML에서 `atchFileId`를 긁어 쓰면 **다른 첨부를** 받는다. 실제로
그렇게 해서 무관한 zip을 받았다(notes/013). 1단계를 반드시 거쳐야 한다.

`publicDataDetailPk`(uddi)는 상세 페이지의 `fn_fileDataDown(...)` 호출에서
읽는다. 데이터셋이 갱신되면 바뀔 수 있어 상수로 두고 인자로 덮을 수 있게 했다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import requests

PUBLIC_DATA_PK = "15067467"
PUBLIC_DATA_DETAIL_PK = "uddi:0add74e2-fe8c-4807-b300-814233aad8ea"
META_URL = "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do"
FILE_URL = "https://www.data.go.kr/cmm/cmm/fileDownload.do"
USER_AGENT = "clausegraph/0.1 (portfolio research)"

# 심평원이 CP949로 내려준다. UTF-8로 읽으면 첫 바이트에서 깨진다.
SOURCE_ENCODING = "cp949"
TIMEOUT_SEC = 300

CSV_FILENAME = "sick_master.csv"
META_FILENAME = "sick_master.meta.json"
CODE_COLUMN = "상병기호"


class KcdDownloadError(RuntimeError):
    """코드표를 받지 못했다."""


def fetch_metadata(
    session: requests.Session,
    public_data_pk: str = PUBLIC_DATA_PK,
    detail_pk: str = PUBLIC_DATA_DETAIL_PK,
) -> dict[str, object]:
    """1단계 — 진짜 첨부 식별자와 행 수를 받는다."""
    response = session.post(
        META_URL,
        data={
            "publicDataPk": public_data_pk,
            "publicDataDetailPk": detail_pk,
            "fileDetailSn": "1",
        },
        timeout=TIMEOUT_SEC,
    )
    response.raise_for_status()
    body = response.json()
    info = body.get("dataSetFileDetailInfo") or {}
    atch_file_id = body.get("atchFileId") or info.get("atchFileId")
    if not atch_file_id:
        raise KcdDownloadError(f"메타데이터에 atchFileId가 없다: {list(body)}")
    return {
        "atch_file_id": atch_file_id,
        "row_count": info.get("atchFileCo"),
        "extension": info.get("atchFileExtsn"),
        "data_name": info.get("dataNm"),
        "institution": info.get("insttNm"),
    }


def download_csv(session: requests.Session, atch_file_id: str, out_path: Path) -> int:
    """2단계 — 파일을 받는다."""
    response = session.get(
        FILE_URL,
        params={"atchFileId": atch_file_id, "fileDetailSn": "1"},
        timeout=TIMEOUT_SEC,
    )
    response.raise_for_status()
    disposition = response.headers.get("Content-Disposition", "")
    if "csv" not in disposition.lower():
        # 다른 첨부를 받은 경우다. 조용히 저장하면 뒤에서 엉뚱하게 터진다.
        raise KcdDownloadError(f"CSV가 아닌 첨부를 받았다: {disposition!r}")
    out_path.write_bytes(response.content)
    return len(response.content)


def verify(csv_path: Path) -> dict[str, object]:
    """받은 파일이 상병마스터인지 확인한다."""
    with csv_path.open(encoding=SOURCE_ENCODING, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    if CODE_COLUMN not in header:
        raise KcdDownloadError(f"{CODE_COLUMN} 컬럼이 없다. 헤더={header[:5]}")
    return {"header": header, "rows": rows}


def run(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    metadata = fetch_metadata(session)
    print(f"{metadata['data_name']} ({metadata['institution']})")
    print(
        f"  첨부 {metadata['atch_file_id']}  형식 {metadata['extension']}  "
        f"행 {metadata['row_count']}"
    )

    csv_path = out_dir / CSV_FILENAME
    size = download_csv(session, str(metadata["atch_file_id"]), csv_path)
    print(f"  받음 {size:,} bytes -> {csv_path}")

    checked = verify(csv_path)
    print(f"  확인 컬럼 {len(checked['header'])}개, 데이터 {checked['rows']:,}행")
    if metadata["row_count"] and int(metadata["row_count"]) != checked["rows"]:
        print(
            f"  주의: 메타데이터 {metadata['row_count']}행 vs 실제 {checked['rows']}행",
            file=sys.stderr,
        )

    (out_dir / META_FILENAME).write_text(
        json.dumps({**metadata, **checked}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  메타데이터 -> {out_dir / META_FILENAME}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="KCD 상병마스터 수집")
    parser.add_argument("--out", type=Path, default=Path("data/kcd"))
    args = parser.parse_args()
    try:
        return run(args.out)
    except (KcdDownloadError, requests.RequestException) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
