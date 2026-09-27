"""회귀 검증 — 재 놓은 수치가 지켜지는지 확인한다.

    uv run --extra ... python -m clausegraph.regress
    uv run python -m clausegraph.regress --record   # 기준선 갱신

## 왜 필요한가

[notes/037](../../notes/037-jd-gap.md)에서 회귀 검증은 **부분**이었다.
단위 테스트 462건이 CI에서 도는데 **지표의 기준선이 없었다.** 면책
recall이 32.9%에서 20%로 떨어져도 CI는 초록이다.

이 프로젝트의 자산은 측정인데 그 측정값이 지켜지지 않았다. 기준선 없이
매번 다시 재는 것은 개선 사이클이 아니다.

## 건너뛴 것은 통과한 것이 아니다

이 파일에서 제일 중요한 규칙이다.

측정 대부분이 그래프·색인·데이터를 요구하는데 `data/law/`는 git에 없고
(라이선스·용량) CI에는 Neo4j도 pgvector도 없다. 그러면 검사가 **조용히
0건 돌고 초록**이 되기 쉽다.

notes/033에서 같은 함정을 이미 밟았다.

> 금액 축이 한 번도 발화하지 않았다. 값을 못 본 사례는 통과한 게 아니라
> **재지 못한 것**이다.

그래서 이 게이트는 셋으로 답한다 — **통과 / 실패 / 못 잼**. 그리고 못 잰
것이 하나라도 있으면 그 사실을 맨 앞에 적는다. `--strict`를 주면 못 잰
것도 실패로 친다(사람이 배포 전에 쓰는 모드).

## 방향이 있는 허용치

`recall`은 떨어지면 안 되고 올라가는 것은 좋다. `leaks`는 늘면 안 된다.
`p50`은 느려지면 안 된다. 그래서 허용치에 **방향**을 준다 — 양쪽으로
±10%를 두면 recall이 10% 떨어져도 통과한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

BASELINE_PATH = Path("eval_baselines.json")


class Direction(StrEnum):
    """어느 쪽으로 움직이면 회귀인가."""

    HIGHER_IS_BETTER = "higher"
    LOWER_IS_BETTER = "lower"
    EXACT = "exact"


class Status(StrEnum):
    PASS = "통과"
    FAIL = "실패"
    SKIP = "못 잼"


@dataclass
class Metric:
    key: str
    direction: Direction
    tolerance: float = 0.0
    description: str = ""


@dataclass
class Check:
    """한 묶음의 측정. 전제가 없으면 통째로 건너뛴다."""

    name: str
    metrics: tuple[Metric, ...]
    requires: tuple[str, ...]
    run: Callable[[], dict[str, float]]


@dataclass
class Outcome:
    check: str
    metric: str
    status: Status
    baseline: float | None = None
    measured: float | None = None
    note: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def failed(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.status is Status.FAIL]

    @property
    def skipped(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.status is Status.SKIP]

    @property
    def passed(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.status is Status.PASS]


# --- 전제 확인 -------------------------------------------------------------
#
# **있는지 없는지만 본다.** 없으면 그 검사를 '못 잼'으로 두고 넘어간다.
# 여기서 예외를 던지면 CI에서 회귀 검증 자체가 실패하는데, 그건 "회귀가
# 있다"와 다른 말이다.


def _has_neo4j() -> bool:
    if not os.environ.get("NEO4J_URI"):
        return False
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:
        return False


def _has_index() -> bool:
    if not os.environ.get("PG_DSN"):
        return False
    try:
        import psycopg

        with psycopg.connect(os.environ["PG_DSN"]) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM clause_chunk WHERE embedding IS NOT NULL")
            return (cursor.fetchone()[0] or 0) > 0
    except Exception:
        return False


def _has_langgraph() -> bool:
    try:
        import langgraph  # noqa: F401

        return True
    except ImportError:
        return False


def _has_eval_set() -> bool:
    return Path("data/eval/exclusion_recall.json").exists()


PREREQUISITES: dict[str, Callable[[], bool]] = {
    "neo4j": _has_neo4j,
    "index": _has_index,
    "langgraph": _has_langgraph,
    "evalset": _has_eval_set,
}


# --- 측정 ------------------------------------------------------------------


def _measure_structure() -> dict[str, float]:
    """소스만 읽어 확인하는 것들. 전제가 없어 **어디서나 돈다.**

    notes/039·040에서 테스트로 못 박은 성질을 여기서도 센다. 테스트와
    겹치지만, 게이트 보고서에 "구조는 확인했다"가 한 줄로 남는 것이
    '못 잼'이 여럿일 때 의미가 있다.
    """
    orchestrator = Path("src/clausegraph/agents/orchestrator.py").read_text(
        encoding="utf-8"
    )
    server = Path("src/clausegraph/mcp_server/server.py").read_text(encoding="utf-8")
    return {
        # 정의 1 + 호출 1 = 2. 늘면 종료 경로가 가드레일을 건너뛸 수 있다.
        "finalize_call_sites": float(orchestrator.count("_finalize(")),
        "mcp_tools": float(server.count("@mcp.tool()")),
        "guarded_tools": float(server.count("@guarded")),
    }


def _measure_recall() -> dict[str, float]:
    from .rag.evaluate import measure_strategies

    return measure_strategies(Path("data/eval/exclusion_recall.json"), k=10)


def _measure_leaks() -> dict[str, float]:
    from .access_eval import measure_totals

    return measure_totals(Path("data/eval/exclusion_recall.json"))


def _measure_agreement() -> dict[str, float]:
    from .agents.compare import measure_agreement

    return measure_agreement(60)


CHECKS: tuple[Check, ...] = (
    Check(
        name="구조",
        metrics=(
            Metric("finalize_call_sites", Direction.EXACT,
                   description="가드레일을 거는 지점 수 (정의 1 + 호출 1)"),
            Metric("mcp_tools", Direction.EXACT, description="MCP 도구 수"),
            Metric("guarded_tools", Direction.EXACT, description="@guarded가 걸린 수"),
        ),
        requires=(),
        run=_measure_structure,
    ),
    Check(
        name="면책 recall",
        metrics=(
            Metric("vector_recall", Direction.HIGHER_IS_BETTER, 0.02),
            Metric("graph_recall", Direction.HIGHER_IS_BETTER, 0.0),
            Metric("vec_graph_recall", Direction.HIGHER_IS_BETTER, 0.02),
            Metric("lexical_recall", Direction.HIGHER_IS_BETTER, 0.02),
        ),
        requires=("neo4j", "index", "evalset"),
        run=_measure_recall,
    ),
    Check(
        name="권한 누수",
        metrics=(
            Metric("leaks_after_gate", Direction.EXACT,
                   description="게이트 후 스코프 밖 노출. 0이 아니면 구멍이다"),
        ),
        requires=("neo4j", "index", "evalset"),
        run=_measure_leaks,
    ),
    Check(
        name="오케스트레이터 일치",
        metrics=(
            Metric("agreement", Direction.HIGHER_IS_BETTER, 0.0,
                   description="자체 vs LangGraph 판정 일치율"),
        ),
        requires=("neo4j", "langgraph"),
        run=_measure_agreement,
    ),
)


def _compare(metric: Metric, baseline: float, measured: float) -> tuple[Status, str]:
    if metric.direction is Direction.EXACT:
        if measured == baseline:
            return Status.PASS, ""
        return Status.FAIL, f"{baseline} -> {measured}"
    if metric.direction is Direction.HIGHER_IS_BETTER:
        if measured >= baseline - metric.tolerance:
            gain = measured - baseline
            return Status.PASS, (f"+{gain:.3f}" if gain > metric.tolerance else "")
        return Status.FAIL, f"{baseline:.3f} -> {measured:.3f} ({measured - baseline:+.3f})"
    if measured <= baseline + metric.tolerance:
        return Status.PASS, ""
    return Status.FAIL, f"{baseline:.3f} -> {measured:.3f} ({measured - baseline:+.3f})"


def run(baseline_path: Path, *, strict: bool, record: bool) -> int:
    baselines: dict[str, dict[str, float]] = (
        json.loads(baseline_path.read_text(encoding="utf-8"))["metrics"]
        if baseline_path.exists()
        else {}
    )
    report = Report()
    recorded: dict[str, dict[str, float]] = {}

    for check in CHECKS:
        missing = [name for name in check.requires if not PREREQUISITES[name]()]
        if missing:
            for metric in check.metrics:
                report.outcomes.append(
                    Outcome(check.name, metric.key, Status.SKIP,
                            note=f"전제 없음: {', '.join(missing)}")
                )
            continue

        measured = check.run()
        recorded[check.name] = measured
        for metric in check.metrics:
            value = measured.get(metric.key)
            if value is None:
                report.outcomes.append(
                    Outcome(check.name, metric.key, Status.SKIP, note="측정값 없음")
                )
                continue
            previous = baselines.get(check.name, {}).get(metric.key)
            if previous is None:
                report.outcomes.append(
                    Outcome(check.name, metric.key, Status.SKIP,
                            measured=value, note="기준선 없음")
                )
                continue
            status, note = _compare(metric, previous, value)
            report.outcomes.append(
                Outcome(check.name, metric.key, status, previous, value, note)
            )

    _print(report, strict)

    if record:
        merged = {**baselines, **recorded}
        baseline_path.write_text(
            json.dumps({"metrics": merged}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n기준선을 {baseline_path}에 적었다 ({len(recorded)}개 묶음).")
        print("**잰 것만 갱신한다** — 못 잰 묶음의 옛 값은 그대로 둔다.")
        return 0

    if report.failed:
        return 1
    if strict and report.skipped:
        return 1
    return 0


def _print(report: Report, strict: bool) -> None:
    print("\n=== 회귀 검증 ===\n")
    if report.skipped:
        print(
            f"**못 잰 항목 {len(report.skipped)}개가 있다.** "
            "건너뛴 것은 통과한 것이 아니다."
        )
        print()

    width = max((len(item.metric) for item in report.outcomes), default=10)
    for item in report.outcomes:
        mark = {Status.PASS: "  ", Status.FAIL: "!!", Status.SKIP: "--"}[item.status]
        value = f"{item.measured:.4g}" if item.measured is not None else "-"
        base = f"{item.baseline:.4g}" if item.baseline is not None else "-"
        print(
            f" {mark} {item.check:14s} {item.metric:{width}s} "
            f"{base:>9s} -> {value:>9s}  {item.status}"
            + (f"  {item.note}" if item.note else "")
        )

    print(
        f"\n통과 {len(report.passed)} · 실패 {len(report.failed)} · "
        f"못 잼 {len(report.skipped)}"
    )
    if report.failed:
        print("\n**회귀가 있다.** 위의 !! 줄을 보라.")
    elif report.skipped and strict:
        print("\n--strict: 못 잰 항목이 있어 실패로 친다.")
    elif report.skipped:
        print("\n잰 것 안에서는 회귀가 없다. 못 잰 것은 여전히 모른다.")
    else:
        print("\n전부 쟀고 회귀가 없다.")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="지표 회귀 검증")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--strict", action="store_true", help="못 잰 항목도 실패로 친다"
    )
    parser.add_argument(
        "--record", action="store_true", help="지금 잰 값을 기준선으로 적는다"
    )
    args = parser.parse_args()
    return run(args.baseline, strict=args.strict, record=args.record)


if __name__ == "__main__":
    raise SystemExit(main())
