"""재무 DB 에서 정답이 확실한 숫자 문항을 자동으로 만든다.

정답을 사람이 적지 않고 DB 값과 계산기로 만들기 때문에 수백 문항도 실수 없이 만들 수 있다.
문장 표현을 섞어 질문 파서가 한 가지 말투에만 맞춰지지 않게 한다.
"""

import random

from dartrag.eval.cases import EvalCase, ExpectedSource
from dartrag.finance.accounts import METRICS, STATEMENT_SJ_DIV
from dartrag.finance.calc import fmt_pct, fmt_won, growth, ratio
from dartrag.finance.tool import Value, pick_values

AMOUNT_TEMPLATES = [
    "{name} {year}년 {label}은 얼마야?",
    "{name}의 {year}년 연간 {label} 알려줘",
    "{year}년 {name} {label}",
]
GROWTH_TEMPLATES = [
    "{name} {year}년 {label} 증가율은?",
    "{name}의 {year}년 {label}은 전년보다 얼마나 변했어?",
]
MARGIN_TEMPLATES = ["{name} {year}년 영업이익률은?", "{year}년 {name} 영업이익률 알려줘"]
UNANSWERABLE_TEMPLATES = [
    "{name} 주가는 내년에 오를까?",
    "{name} 지금 사도 돼?",
    "{name} 대표이사가 좋아하는 음식은?",
    "{name}의 2035년 매출 전망치는?",
]
METRIC_KEYS = ("revenue", "operating_income", "net_income", "total_assets")


def load_values(repo, corp_codes: list[str]) -> dict[str, dict[tuple[str, int], Value]]:
    out = {}
    for key in METRIC_KEYS:
        m = METRICS[key]
        rows = repo.financial_rows(
            corp_codes,
            "11011",
            STATEMENT_SJ_DIV[m.statement],
            m.account_ids,
            tuple(n.replace(" ", "") for n in m.account_names),
        )
        out[key] = pick_values(rows, m)
    return out


def generate_cases(
    companies: list[tuple[str, str]],
    values: dict[str, dict[tuple[str, int], Value]],
    *,
    per_company: int = 8,
    unanswerable_per_company: int = 1,
    seed: int = 0,
) -> list[EvalCase]:
    rng = random.Random(seed)
    finance_src = [ExpectedSource(finance=True)]
    cases: list[EvalCase] = []
    for corp, name in sorted(companies):
        pool: list[EvalCase] = []
        for key in METRIC_KEYS:
            label = METRICS[key].label
            for (c, year), v in sorted(values[key].items()):
                if c != corp:
                    continue
                pool.append(
                    EvalCase(
                        f"gen-{corp}-{year}-{key}",
                        rng.choice(AMOUNT_TEMPLATES).format(name=name, year=year, label=label),
                        "numeric",
                        corp_codes=[corp],
                        expected_numbers=[fmt_won(v.amount, exact=False)],
                        expected_sources=finance_src,
                        source="generated",
                    )
                )
                prev = values[key].get((corp, year - 1))
                g = growth(v.amount, prev.amount) if prev and key != "total_assets" else None
                if g is not None:
                    pool.append(
                        EvalCase(
                            f"gen-{corp}-{year}-{key}-growth",
                            rng.choice(GROWTH_TEMPLATES).format(name=name, year=year, label=label),
                            "numeric",
                            corp_codes=[corp],
                            expected_numbers=[fmt_pct(g)],
                            expected_sources=finance_src,
                            source="generated",
                        )
                    )
        for (c, year), op in sorted(values["operating_income"].items()):
            rev = values["revenue"].get((c, year))
            if c == corp and rev and (r := ratio(op.amount, rev.amount)) is not None:
                pool.append(
                    EvalCase(
                        f"gen-{corp}-{year}-operating_margin",
                        rng.choice(MARGIN_TEMPLATES).format(name=name, year=year),
                        "numeric",
                        corp_codes=[corp],
                        expected_numbers=[fmt_pct(r)],
                        expected_sources=finance_src,
                        source="generated",
                    )
                )
        if not pool:
            continue  # 재무 데이터가 없는 회사
        cases += rng.sample(pool, min(per_company, len(pool)))
        for i, t in enumerate(rng.sample(UNANSWERABLE_TEMPLATES, unanswerable_per_company)):
            cases.append(
                EvalCase(
                    f"gen-{corp}-unanswerable-{i}",
                    t.format(name=name),
                    "unanswerable",
                    corp_codes=[corp],
                    source="generated",
                )
            )
    return cases
