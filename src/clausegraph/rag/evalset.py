"""면책 recall 평가셋.

질문은 **청구자 말투**로 썼다. 약관 문구를 그대로 쓰면 벡터가 그 조항을
바로 찾아내 실험이 무의미해진다. 실제 청구는 "임플란트를 했는데 나오나요"
처럼 들어오고, 의미상 가장 가까운 글은 보장 조항이다. 지급 여부를 가르는
면책 조항은 다른 곳에 흩어져 있다.

정답(gold)은 키워드로 조문을 지목해 두고 색인에서 uid를 확정한다. 키워드는
사람이 조항을 확인하고 고른 것이고, 검색 전략은 이 키워드를 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

DOMESTIC_NON_BENEFIT = (
    "실손의료보험 특별약관1(중증 비급여 실손의료비)",
    "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
)
DOMESTIC_BENEFIT = ("기본형 실손의료보험(급여 실손의료비)",)
ACCIDENT_HEALTH = ("질병·상해보험(손해보험 회사용)",)
LIFE = ("생명보험",)


@dataclass(frozen=True)
class Question:
    qid: int
    query: str
    gold_keyword: str
    products: tuple[str, ...]
    note: str


QUESTIONS: tuple[Question, ...] = (
    Question(
        qid=1,
        query="고도비만이라 위 소매절제 수술을 받았습니다. 비급여 진료비가 실손에서 나오나요?",
        gold_keyword="비만(E66)",
        products=DOMESTIC_NON_BENEFIT,
        note="비만은 면책",
    ),
    Question(
        qid=2,
        query="출산 뒤 기침할 때 소변이 새서 교정 시술을 받았습니다. 비급여 비용 청구가 되나요?",
        gold_keyword="요실금",
        products=DOMESTIC_NON_BENEFIT,
        note="요실금은 면책",
    ),
    Question(
        qid=3,
        query="치핵 수술을 받았는데 비급여 항목이 있었습니다. 실손 청구 가능한가요?",
        gold_keyword="직장 또는 항문",
        products=DOMESTIC_NON_BENEFIT,
        note="직장·항문 질환은 면책",
    ),
    Question(
        qid=4,
        query="우울증으로 정신과 상담과 약물치료를 받고 있습니다. 비급여 진료비가 보상되나요?",
        gold_keyword="정신 및 행동장애",
        products=DOMESTIC_NON_BENEFIT,
        note="F04~F99는 면책(일부 예외)",
    ),
    Question(
        qid=5,
        query="임신 중 심한 입덧으로 입원했습니다. 비급여로 청구된 금액을 받을 수 있나요?",
        gold_keyword="산후기",
        products=DOMESTIC_NON_BENEFIT,
        note="임신·출산·산후기는 면책",
    ),
    Question(
        qid=6,
        query="시험관 시술을 받다가 합병증이 생겨 치료했습니다. 비급여 비용이 나오나요?",
        gold_keyword="습관성 유산",
        products=DOMESTIC_NON_BENEFIT,
        note="불임·인공수정 관련 합병증은 면책",
    ),
    Question(
        qid=7,
        query="아이가 태어날 때부터 뇌에 이상이 있어 치료 중입니다. 비급여 진료비 청구가 되나요?",
        gold_keyword="선천성 뇌질환",
        products=DOMESTIC_NON_BENEFIT,
        note="선천성 뇌질환은 면책",
    ),
    Question(
        qid=8,
        query="충치가 심해 임플란트를 했습니다. 비급여 진료비를 실손으로 받을 수 있나요?",
        gold_keyword="치과치료",
        products=DOMESTIC_NON_BENEFIT,
        note="치과치료(K00~K08)는 면책",
    ),
    Question(
        qid=9,
        query="기력이 떨어져 영양수액을 맞았습니다. 비급여인데 청구되나요?",
        gold_keyword="영양제, 비타민제",
        products=DOMESTIC_NON_BENEFIT,
        note="영양제·비타민제는 면책",
    ),
    Question(
        qid=10,
        query="키 성장을 위해 성장호르몬 주사를 맞고 있습니다. 비급여 비용이 보상되나요?",
        gold_keyword="호르몬 투여",
        products=DOMESTIC_NON_BENEFIT,
        note="호르몬 투여는 면책",
    ),
    Question(
        qid=11,
        query="발목 인대를 다쳐 압박 고정용 보호대를 구입했습니다. 그 비용이 나오나요?",
        gold_keyword="보조기 등 진료",
        products=DOMESTIC_NON_BENEFIT,
        note="보조기 구입비는 면책",
    ),
    Question(
        qid=12,
        query="입원 중 간병인을 쓰고 진단서 발급비도 냈습니다. 청구할 수 있나요?",
        gold_keyword="간병",
        products=DOMESTIC_NON_BENEFIT,
        note="간병비·증명료는 면책",
    ),
    Question(
        qid=13,
        query="산재로 치료받았는데 산재에서 안 준 본인부담분을 실손에 청구할 수 있나요?",
        gold_keyword="산재보험에서 보상",
        products=DOMESTIC_NON_BENEFIT,
        note="산재 보상분은 면책, 본인부담분은 예외",
    ),
    Question(
        qid=14,
        query="의사는 통원해도 된다는데 제가 원해서 입원했습니다. 입원 의료비가 나오나요?",
        gold_keyword="자의적으로 입원",
        products=DOMESTIC_BENEFIT,
        note="자의 입원은 면책",
    ),
    Question(
        qid=15,
        query="암벽등반 동호회 활동 중 추락해 골절상을 입었습니다. 상해보험금이 나오나요?",
        gold_keyword="전문등반",
        products=ACCIDENT_HEALTH,
        note="전문등반 중 사고는 면책",
    ),
    Question(
        qid=16,
        query="오토바이 경주 대회에 나갔다가 사고로 크게 다쳤습니다. 보험금 지급이 되나요?",
        gold_keyword="흥행",
        products=ACCIDENT_HEALTH,
        note="경기·시범·흥행 중 사고는 면책",
    ),
    Question(
        qid=17,
        query="원양어선 선원으로 일하다 배 위에서 작업 중 다쳤습니다. 보험금을 받을 수 있나요?",
        gold_keyword="선박에 탑승",
        products=ACCIDENT_HEALTH,
        note="직무상 선박 탑승 중 사고는 면책",
    ),
    Question(
        qid=18,
        query="가입하고 3년 뒤에 스스로 목숨을 끊었습니다. 사망보험금이 지급되나요?",
        gold_keyword="고의로 자신을 해친",
        products=LIFE,
        note="고의 자해는 면책이나 2년 경과 자살은 예외",
    ),
)
