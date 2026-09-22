"""용어→코드 변환을 규칙과 로컬 LLM으로 재 본다.

    uv run --extra graph python -m clausegraph.llm.evaluate

## 무엇을 맞았다고 볼 것인가

코드를 글자까지 똑같이 맞히라고 하면 지나치다. 충치를 K02로 적든 K03으로
적든 `치과치료(K00~K08)` 면책은 똑같이 걸린다. 판정에 영향을 주는 것은
**코드가 약관이 못박은 구간에 들어가는지**다. 그래서 기대값을 구간으로 두고
`agents/kcd.py`의 대조 로직으로 채점한다.

## 두 종류의 오류를 따로 센다

- **놓침** — 걸려야 하는 면책이 안 걸린다. 지급하면 안 되는 건에 돈이 나간다.
- **헛짚음** — 코드가 없어야 하는 청구에 코드를 붙인다. 지급해야 하는 건이
  부지급된다.

지급심사에서 둘의 무게가 다르므로 하나의 정확도로 합치지 않는다.

평가 케이스는 코드 안에서 합성한다. 실제 청구 데이터를 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from statistics import median

from ..agents import terminology
from ..agents.kcd import CodeRange
from ..observability import Registry
from .client import LlmUnavailableError
from .coder import CodingResult, code_claim, select_options
from .providers import Backend, discover


@dataclass(frozen=True)
class Case:
    narrative: str
    # 기대 구간. None이면 코드가 없어야 한다.
    expected: CodeRange | None
    note: str


CASES: tuple[Case, ...] = (
    Case("충치가 심해 임플란트를 했습니다", CodeRange("K", 0, 8), "치과치료 K00~K08"),
    Case(
        "우울증으로 정신과 통원치료를 받고 있습니다",
        CodeRange("F", 4, 99),
        "정신 및 행동장애",
    ),
    Case(
        "기침할 때 소변이 새서 요실금 교정 시술을 받았습니다",
        CodeRange("N", 39, 39),
        "요실금",
    ),
    Case("치핵 수술을 받았습니다", CodeRange("K", 60, 64), "직장·항문 질환"),
    Case("고도비만으로 위 절제 수술을 받았습니다", CodeRange("E", 66, 66), "비만"),
    Case("임신 중 심한 입덧으로 입원했습니다", CodeRange("O", 0, 99), "임신·출산·산후기"),
    Case(
        "아이가 태어날 때부터 뇌에 이상이 있습니다",
        CodeRange("Q", 0, 4),
        "선천성 뇌질환",
    ),
    Case(
        "시험관 시술을 받다가 합병증이 생겼습니다",
        CodeRange("N", 96, 98),
        "불임 관련 합병증",
    ),
    # 아래는 코드가 붙으면 안 되는 청구다.
    Case("입원 중 간병인을 썼습니다", None, "간병비 — 코드로 적히지 않은 면책"),
    Case("기력이 떨어져 영양수액을 맞았습니다", None, "영양제 — 코드 없는 면책"),
    Case("보험료를 더 냈으니 돌려주세요", None, "보험금 청구가 아니다"),
    Case("진단서 발급비를 청구합니다", None, "증명료 — 코드 없는 면책"),
)

# 면책에 걸리지 않아야 하는 상병. 코드는 나와야 하지만 면책 구간이
# 아니어야 한다 — 헛짚음 검사용.
CONTROL_CASES: tuple[Case, ...] = (
    Case(
        "계단에서 넘어져 발목 골절 진단을 받았습니다",
        CodeRange("S", 82, 82),
        "상해 — 보장 대상",
    ),
    Case("폐렴으로 4일간 입원했습니다", CodeRange("J", 18, 18), "질병 — 보장 대상"),
)


# 코드를 생성하게 하는 대신 목록에서 고르게 하는 실험용.
# 라벨은 실손 비급여 면책 조항에서 뽑은 핵심어다.
SELECT_OPTIONS: tuple[str, ...] = (
    "정신 및 행동장애",
    "불임·인공수정 관련 합병증",
    "임신·출산·산후기",
    "선천성 뇌질환",
    "비만",
    "요실금",
    "직장 또는 항문 질환",
    "치과치료 및 한방치료",
    "영양제·비타민제",
    "호르몬 투여",
    "보조기 등 진료재료 구입비",
    "간병비·증명서 발급비",
    "산재보험에서 보상받는 의료비",
)

# (서술, 정답 번호). None이면 아무것도 고르지 않아야 한다.
SELECT_CASES: tuple[tuple[str, int | None], ...] = (
    ("충치가 심해 임플란트를 했습니다", 8),
    ("우울증으로 정신과 통원치료를 받고 있습니다", 1),
    ("기침할 때 소변이 새서 요실금 교정 시술을 받았습니다", 6),
    ("치핵 수술을 받았습니다", 7),
    ("고도비만으로 위 절제 수술을 받았습니다", 5),
    ("임신 중 심한 입덧으로 입원했습니다", 3),
    ("아이가 태어날 때부터 뇌에 이상이 있습니다", 4),
    ("시험관 시술을 받다가 합병증이 생겼습니다", 2),
    ("입원 중 간병인을 썼습니다", 12),
    ("기력이 떨어져 영양수액을 맞았습니다", 9),
    ("키 성장을 위해 성장호르몬 주사를 맞습니다", 10),
    ("발목 보호대를 구입했습니다", 11),
    ("보험료를 더 냈으니 돌려주세요", None),
    ("계단에서 넘어져 발목 골절 진단을 받았습니다", None),
    ("폐렴으로 4일간 입원했습니다", None),
)


@dataclass
class Score:
    hit: int = 0
    miss: int = 0
    false_code: int = 0
    correct_none: int = 0
    latencies: list[float] = field(default_factory=list)
    # 붙지 못한 호출. 0이 아니면 그 단의 점수는 읽지 않는다.
    errors: int = 0

    @property
    def p50(self) -> float:
        return median(self.latencies) if self.latencies else 0.0


@dataclass
class BackendResult:
    """한 단의 측정 결과. 표를 만들 때 쓴다."""

    label: str
    onprem: bool
    generation: Score
    selection: Score


def score(cases: tuple[Case, ...], produce, registry: Registry, label: str) -> Score:
    result = Score()
    for case in cases:
        started = time.perf_counter()
        try:
            codes = produce(case.narrative)
        except LlmUnavailableError as exc:
            # 붙지 못한 것은 틀린 것과 다르다. 따로 센다 — 0이 아니면
            # 그 단의 점수를 읽지 않는다.
            result.errors += 1
            print(f"  ! {case.narrative[:24]} — {exc}")
            continue
        elapsed = (time.perf_counter() - started) * 1000
        registry.record(label, elapsed)
        result.latencies.append(elapsed)

        if case.expected is None:
            if codes:
                result.false_code += 1
            else:
                result.correct_none += 1
            continue

        if any(case.expected.contains(code) for code in codes):
            result.hit += 1
        else:
            result.miss += 1
    return result


def report(name: str, result: Score, cases: tuple[Case, ...]) -> None:
    expected = [case for case in cases if case.expected is not None]
    none_cases = [case for case in cases if case.expected is None]
    print(f"\n--- {name}")
    if result.errors:
        print(f"  붙지 못한 호출 {result.errors}건 — 아래 점수는 읽지 않는다")
    if expected:
        print(f"  구간 적중 {result.hit}/{len(expected)}   놓침 {result.miss}")
    if none_cases:
        print(
            f"  코드 없어야 함 {result.correct_none}/{len(none_cases)}   "
            f"헛짚음 {result.false_code}"
        )


def score_selection(backend: Backend, registry: Registry) -> Score:
    """코드를 생성하는 대신 면책 목록에서 고르게 해 본다."""
    result = Score()
    for narrative, expected in SELECT_CASES:
        started = time.perf_counter()
        try:
            picked, _ = select_options(
                narrative, list(SELECT_OPTIONS), backend.client, fallback=False
            )
        except LlmUnavailableError as exc:
            result.errors += 1
            print(f"  ! {narrative[:24]} — {exc}")
            continue
        elapsed = (time.perf_counter() - started) * 1000
        registry.record(f"{backend.key} 선택", elapsed)
        result.latencies.append(elapsed)

        if expected is None:
            if picked:
                result.false_code += 1
            else:
                result.correct_none += 1
        elif expected in picked:
            result.hit += 1
        else:
            result.miss += 1
    return result


def report_selection(name: str, result: Score) -> None:
    positives = sum(1 for _, expected in SELECT_CASES if expected is not None)
    negatives = len(SELECT_CASES) - positives
    print(f"\n--- {name} — 생성이 아니라 선택")
    if result.errors:
        print(f"  붙지 못한 호출 {result.errors}건 — 아래 점수는 읽지 않는다")
    print(f"  적중 {result.hit}/{positives}   놓침 {result.miss}")
    print(
        f"  아무것도 고르지 않아야 함 {result.correct_none}/{negatives}   "
        f"헛짚음 {result.false_code}"
    )


def measure(backend: Backend, registry: Registry, verbose: bool) -> BackendResult:
    """한 단을 잰다. 규칙 표로 내려가지 않게 `fallback=False`로 부른다."""
    all_cases = CASES + CONTROL_CASES

    # 채점하면서 원문을 챙겨 둔다. 전에는 `--verbose`가 같은 케이스를 한 번
    # 더 불렀는데, 그 때문에 호출이 두 배가 되어 무료 티어 한도를 넘겼다.
    # 볼 것을 이미 받아 놓고 다시 물을 이유가 없다.
    seen: list[tuple[str, CodingResult]] = []

    def by_llm(narrative: str) -> tuple[str, ...]:
        outcome = code_claim(narrative, backend.client, fallback=False)
        seen.append((narrative, outcome))
        return outcome.codes

    generation = score(all_cases, by_llm, registry, backend.key)
    report(backend.label, generation, all_cases)

    if verbose:
        print("\n--- 응답 원문")
        for narrative, outcome in seen:
            dropped = f"  버림={list(outcome.dropped)}" if outcome.dropped else ""
            print(f"  {narrative[:34]:36s} -> {list(outcome.codes)}{dropped}")
            if outcome.raw and outcome.raw != ", ".join(outcome.codes):
                print(f"      원문 {outcome.raw[:70]!r}")

    selection = score_selection(backend, registry)
    report_selection(backend.label, selection)
    return BackendResult(backend.label, backend.onprem, generation, selection)


def summary(rules: Score, results: list[BackendResult]) -> None:
    """한 표로 모은다.

    폐쇄망 열을 정확도 옆에 둔다 — 이 둘을 같이 봐야 결정이 나온다.
    프론티어가 이겨도 들여놓을 수 없으면 결정은 바뀌지 않는다.
    """
    cases = CASES + CONTROL_CASES
    positives = sum(1 for case in cases if case.expected is not None)
    negatives = len(cases) - positives
    sel_pos = sum(1 for _, expected in SELECT_CASES if expected is not None)
    sel_neg = len(SELECT_CASES) - sel_pos

    print("\n\n=== 요약")
    print(
        f"{'방식':26s} {'생성 적중':>10s} {'헛짚음':>8s} "
        f"{'선택 적중':>10s} {'선택 헛짚음':>12s} {'p50':>10s}  온프렘"
    )
    print("-" * 94)
    print(
        f"{'규칙 표':26s} {f'{rules.hit}/{positives}':>10s} "
        f"{f'{rules.false_code}/{negatives}':>8s} {'-':>10s} {'-':>12s} "
        f"{f'{rules.p50:.1f}ms':>10s}  예"
    )
    for item in results:
        gen, sel = item.generation, item.selection
        gen_cell = "붙지 못함" if gen.errors else f"{gen.hit}/{positives}"
        sel_cell = "붙지 못함" if sel.errors else f"{sel.hit}/{sel_pos}"
        print(
            f"{item.label[:26]:26s} {gen_cell:>10s} "
            f"{f'{gen.false_code}/{negatives}':>8s} {sel_cell:>10s} "
            f"{f'{sel.false_code}/{sel_neg}':>12s} {f'{gen.p50:.1f}ms':>10s}  "
            f"{'예' if item.onprem else '아니오'}"
        )


def run(verbose: bool, only: str | None) -> int:
    backends = discover()
    if only:
        backends = tuple(item for item in backends if item.key == only)
        if not backends:
            print(f"'{only}' 단이 환경에 없다. 키를 .env에 넣었는지 보라.")
            return 1

    registry = Registry()
    all_cases = CASES + CONTROL_CASES

    rules = score(all_cases, terminology.lookup, registry, "규칙")
    report("규칙 표 (agents/terminology.py)", rules, all_cases)

    results: list[BackendResult] = []
    for backend in backends:
        if not backend.client.available():
            print(f"\n--- {backend.label} — 붙지 않았다 ({backend.client.base_url})")
            continue
        results.append(measure(backend, registry, verbose))

    if not results:
        print("\n어느 단에도 붙지 못해 규칙 표만 측정했다.")
        print("로컬은 docs/local-llm.md, 원격 키는 .env.example 참고.")

    summary(rules, results)
    print()
    print(registry.report("변환 지연"))
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="용어→코드 변환 평가")
    parser.add_argument("--verbose", action="store_true", help="LLM 응답 원문도 찍는다")
    parser.add_argument(
        "--provider",
        choices=("local", "groq", "gemini"),
        help="한 단만 잰다. 기본은 환경에 준비된 단 전부",
    )
    args = parser.parse_args()
    return run(args.verbose, args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
