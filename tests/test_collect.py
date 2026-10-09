from datetime import date

from dartrag.dart.models import Corp, Filing, FinancialRow
from dartrag.pipeline.collect import collect
from dartrag.storage import LocalRawStore


class FakeClient:
    def __init__(self):
        self.document_calls = []
        self.fs_calls = []

    def corp_codes(self):
        return [
            Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            Corp(corp_code="99999999", corp_name="비상장"),
        ]

    def iter_filings(self, corp_code, start, end):
        names = [
            ("20250311000001", "사업보고서 (2024.12)"),
            ("20240514000002", "[기재정정]분기보고서 (2024.03)"),
            ("20200316000003", "사업보고서 (2019.12)"),  # 범위 밖
        ]
        for no, name in names:
            yield Filing(
                corp_code=corp_code,
                corp_name="삼성전자",
                report_nm=name,
                rcept_no=no,
                rcept_dt=date(2024, 1, 1),
            )

    def document(self, rcept_no):
        self.document_calls.append(rcept_no)
        return b"PK" + rcept_no.encode()

    def financial_statements(self, corp_code, bsns_year, reprt_code, fs_div):
        self.fs_calls.append((bsns_year, reprt_code, fs_div))
        if fs_div == "OFS":
            return []
        return [
            FinancialRow(
                rcept_no="20250311000001",
                reprt_code=reprt_code,
                bsns_year=str(bsns_year),
                corp_code=corp_code,
                sj_div="IS",
                sj_nm="손익계산서",
                account_id="dart_OperatingIncomeLoss",
                account_nm=" 영업이익 ",
                thstrm_amount="32,725,961,000,000",
                ord="5",
                currency="KRW",
            )
        ]


class FakeRepo:
    def __init__(self):
        self.companies = []
        self.filings = {}
        self.financials = {}

    def upsert_companies(self, corps):
        self.companies = list(corps)

    def fiscal_end_month(self, corp_code):
        return 12

    def upsert_filing(self, filing, report, reprt_code, raw_key):
        self.filings[filing.rcept_no] = (report.period_key, reprt_code, raw_key)

    def replace_financials(self, corp_code, bsns_year, reprt_code, fs_div, items):
        self.financials[(corp_code, bsns_year, reprt_code, fs_div)] = items
        return len(items)


def run(tmp_path, client=None, stocks=("005930",)):
    client = client or FakeClient()
    repo = FakeRepo()
    store = LocalRawStore(tmp_path)
    summary = collect(client, repo, store, list(stocks), 2022, 2024, today=date(2025, 6, 1))
    return client, repo, summary


def test_collects_filings_documents_and_financials(tmp_path):
    client, repo, summary = run(tmp_path)

    assert [c.corp_code for c in repo.companies] == ["00126380"]  # 상장사만
    assert repo.filings == {
        "20250311000001": ("2024.12", "11011", "documents/2025/20250311000001.zip"),
        "20240514000002": ("2024.03", "11013", "documents/2024/20240514000002.zip"),
    }
    assert summary.filings == 2 and summary.documents_downloaded == 2
    item = repo.financials[("00126380", 2024, "11011", "CFS")][0]
    assert item["amount"] == 32_725_961_000_000
    assert item["account_nm"] == "영업이익"
    assert item["ord"] == 5
    assert ("00126380", 2024, "11011", "OFS") not in repo.financials  # 별도 없음
    assert summary.errors == []


def test_documents_are_cached(tmp_path):
    run(tmp_path)
    client, _, summary = run(tmp_path)
    assert client.document_calls == []
    assert summary.documents_cached == 2


def test_unknown_stock_is_reported(tmp_path):
    _, _, summary = run(tmp_path, stocks=("005930", "123456"))
    assert summary.errors == ["123456: 상장사 목록에 없음"]
