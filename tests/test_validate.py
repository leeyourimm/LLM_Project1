from dartrag.finance.validate import check_values

T = 10**12
A = "11011"


def vals(**metrics):
    base = {"revenue": {}, "total_assets": {}, "total_liabilities": {}, "total_equity": {}}
    for k, v in metrics.items():
        base[k] = v
    return base


def rules(issues):
    return sorted((i.bsns_year, i.reprt_code, i.rule, i.severity) for i in issues)


def test_clean_data_has_no_issues():
    k = (2024, A, "CFS")
    v = vals(
        revenue={k: 300 * T, (2023, A, "CFS"): 259 * T},
        total_assets={k: 514 * T, (2023, A, "CFS"): 455 * T},
        total_liabilities={k: 112 * T},
        total_equity={k: 402 * T},
    )
    assert check_values("c", v) == []


def test_balance_negative_unit_missing_and_jump():
    v = vals(
        revenue={
            (2023, A, "CFS"): 10 * T,
            (2024, A, "CFS"): 50 * T,  # 5배
            (2024, "11013", "OFS"): -1,
        },
        total_assets={(2024, A, "CFS"): 100 * T, (2022, A, "OFS"): 5_000, (2021, A, "OFS"): 0},
        total_liabilities={(2024, A, "CFS"): 40 * T},
        total_equity={(2024, A, "CFS"): 50 * T},  # 10% 어긋남
    )
    got = rules(check_values("c", v))
    assert (2024, A, "balance", "error") in got
    assert (2024, A, "jump", "warn") in got
    assert (2024, "11013", "negative", "error") in got
    assert (2022, A, "unit", "warn") in got
    assert (2021, A, "negative", "error") in got
    assert (2023, A, "missing", "warn") in got  # 자산총계 없음
    assert (2022, A, "missing", "warn") in got  # 매출액 없음
    detail = next(i.detail for i in check_values("c", v) if i.rule == "balance")
    assert "차이 10.0%" in detail
