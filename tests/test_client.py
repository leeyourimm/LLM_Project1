from datetime import date

import httpx
import pytest
import respx

from dartrag.dart import DartApiError, DartHttpError, OpenDartClient
from dartrag.dart.client import BASE_URL


@pytest.fixture
def client():
    c = OpenDartClient("test-key", min_interval=0, sleep=lambda _: None)
    yield c
    c.close()


@respx.mock
def test_corp_codes(client, corp_zip):
    respx.get(f"{BASE_URL}/corpCode.xml").respond(content=corp_zip)
    corps = client.corp_codes()
    assert [c.corp_name for c in corps] == ["삼성전자", "에스케이하이닉스", "비상장회사"]
    assert [c.is_listed for c in corps] == [True, True, False]
    assert corps[0].modify_date == date(2024, 1, 1)


def _filing(no: int) -> dict:
    return {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "corp_cls": "Y",
        "report_nm": "사업보고서 (2023.12)",
        "rcept_no": f"2024031100{no:04d}",
        "flr_nm": "삼성전자",
        "rcept_dt": "20240312",
        "rm": "",
    }


@respx.mock
def test_iter_filings_paginates(client):
    route = respx.get(f"{BASE_URL}/list.json")
    route.side_effect = [
        httpx.Response(200, json={"status": "000", "total_page": 2, "list": [_filing(1)]}),
        httpx.Response(200, json={"status": "000", "total_page": 2, "list": [_filing(2)]}),
    ]
    filings = list(client.iter_filings("00126380", date(2023, 1, 1), date(2024, 12, 31)))
    assert [f.rcept_no for f in filings] == ["20240311000001", "20240311000002"]
    first = route.calls[0].request.url.params
    assert first["last_reprt_at"] == "Y"
    assert first["pblntf_ty"] == "A"
    assert first["crtfc_key"] == "test-key"


@respx.mock
def test_no_data_is_empty(client):
    respx.get(f"{BASE_URL}/list.json").respond(json={"status": "013", "message": "없음"})
    assert list(client.iter_filings("00126380", date(2023, 1, 1), date(2023, 2, 1))) == []
    respx.get(f"{BASE_URL}/fnlttSinglAcntAll.json").respond(
        json={"status": "013", "message": "없음"}
    )
    assert client.financial_statements("00126380", 2023, "11011") == []


@respx.mock
def test_rate_limit_is_retried(client):
    route = respx.get(f"{BASE_URL}/list.json")
    route.side_effect = [
        httpx.Response(200, json={"status": "020", "message": "요청 제한 초과"}),
        httpx.Response(200, json={"status": "000", "total_page": 1, "list": [_filing(1)]}),
    ]
    assert len(list(client.iter_filings("00126380", date(2023, 1, 1), date(2024, 1, 1)))) == 1
    assert route.call_count == 2


@respx.mock
def test_invalid_key_raises(client):
    body = {"status": "010", "message": "등록되지 않은 키"}
    respx.get(f"{BASE_URL}/list.json").respond(json=body)
    with pytest.raises(DartApiError) as e:
        list(client.iter_filings("00126380", date(2023, 1, 1), date(2024, 1, 1)))
    assert e.value.status == "010"


@respx.mock
def test_document_error_body_is_raised(client):
    xml = b"<result><status>014</status><message>file not found</message></result>"
    respx.get(f"{BASE_URL}/document.xml").respond(content=xml)
    with pytest.raises(DartApiError) as e:
        client.document("20240311000001")
    assert e.value.status == "014"


@respx.mock
def test_server_error_is_retried(client):
    route = respx.get(f"{BASE_URL}/document.xml")
    route.side_effect = [httpx.Response(503), httpx.Response(200, content=b"PK\x03\x04data")]
    assert client.document("20240311000001").startswith(b"PK")


def test_requires_key():
    with pytest.raises(ValueError):
        OpenDartClient("")


@respx.mock
def test_iter_filings_all_companies(client):
    route = respx.get(f"{BASE_URL}/list.json").respond(
        json={"status": "000", "total_page": 1, "list": [_filing(1)]}
    )
    got = list(
        client.iter_filings(
            None, date(2025, 3, 1), date(2025, 3, 2), pblntf_ty="B", final_only=False
        )
    )
    assert len(got) == 1
    params = route.calls[0].request.url.params
    assert "corp_code" not in params
    assert params["pblntf_ty"] == "B" and params["last_reprt_at"] == "N"


@respx.mock
def test_http_errors_do_not_leak_key(client):
    respx.get(f"{BASE_URL}/list.json").respond(status_code=500)
    with pytest.raises(DartHttpError) as e:
        list(client.iter_filings("00126380", date(2023, 1, 1), date(2023, 2, 1)))
    # httpx 의 오류 메시지에는 crtfc_key 가 든 주소가 들어 있다. 작업 기록·로그로 퍼지지 않게 뺀다
    assert "test-key" not in str(e.value) and "HTTP 500" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__

    respx.get(f"{BASE_URL}/fnlttSinglAcntAll.json").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(DartHttpError) as e:
        client.financial_statements("00126380", 2023, "11011")
    assert "ConnectError" in str(e.value) and "test-key" not in str(e.value)
