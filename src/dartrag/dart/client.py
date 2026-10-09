"""OpenDART API 클라이언트.

https://opendart.fss.or.kr/guide/main.do
"""

import io
import time
import zipfile
from collections.abc import Iterator
from datetime import date

import httpx
from lxml import etree

from dartrag.dart.models import Corp, Filing, FinancialRow

BASE_URL = "https://opendart.fss.or.kr/api"

STATUS_OK = "000"
STATUS_NO_DATA = "013"
# 재시도하면 풀릴 수 있는 상태: 요청 제한 초과, 시스템 점검
RETRYABLE_STATUSES = {"020", "800"}


class DartApiError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(f"OpenDART {status}: {message}")
        self.status = status


class DartHttpError(RuntimeError):
    """재시도해도 안 된 HTTP·연결 오류. httpx 오류 메시지에는 인증키가 든 주소가 찍혀서
    (작업 기록, 로그, 오류 수집으로 퍼진다) 경로와 상태 코드만 남긴다."""

    def __init__(self, path: str, detail: str):
        super().__init__(f"OpenDART {path} 요청 실패: {detail}")
        self.path = path


class QuotaExceeded(DartApiError):
    """오늘 쓰기로 정한 호출 수를 다 썼다 (OpenDART 하루 한도는 키당 20,000회)."""

    def __init__(self, used: int, limit: int):
        super().__init__("020", f"오늘 호출 한도 {limit}회 중 {used}회를 썼습니다")


class OpenDartClient:
    def __init__(
        self,
        api_key: str,
        *,
        min_interval: float = 0.2,
        max_retries: int = 4,
        http: httpx.Client | None = None,
        sleep=time.sleep,
        quota=None,
        shared_throttle=None,
    ):
        if not api_key:
            raise ValueError("DART_API_KEY 가 비어 있습니다")
        self._key = api_key
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=60)
        self._sleep = sleep
        self._last_call = 0.0
        # 여러 작업자가 같은 키를 나눠 쓸 때 하루 호출 수를 함께 센다 (dart/quota.py)
        self._quota = quota
        self._shared = shared_throttle

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- 저수준 호출 -------------------------------------------------------

    def _throttle(self) -> None:
        if self._shared is not None:
            self._shared.wait()
            return
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict) -> httpx.Response:
        params = {"crtfc_key": self._key, **params}
        for attempt in range(self._max_retries + 1):
            if self._quota is not None:
                self._quota.use()
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
                resp.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == self._max_retries:
                    detail = (
                        f"HTTP {e.response.status_code}"
                        if isinstance(e, httpx.HTTPStatusError)
                        else type(e).__name__
                    )
                    raise DartHttpError(path, detail) from None
                self._sleep(2**attempt)
                continue
            status = _error_status(resp)
            if status in RETRYABLE_STATUSES and attempt < self._max_retries:
                self._sleep(2**attempt)
                continue
            return resp
        raise AssertionError("unreachable")

    def _get_json(self, path: str, params: dict) -> dict | None:
        """정상이면 JSON, 데이터 없음(013)이면 None, 그 외 오류는 예외."""
        data = self._get(path, params).json()
        status = data.get("status")
        if status == STATUS_NO_DATA:
            return None
        if status != STATUS_OK:
            raise DartApiError(status, data.get("message", ""))
        return data

    def _get_zip(self, path: str, params: dict) -> bytes:
        resp = self._get(path, params)
        if not resp.content.startswith(b"PK"):
            status, message = _parse_error_body(resp)
            raise DartApiError(status, message)
        return resp.content

    # --- API ---------------------------------------------------------------

    def corp_codes(self) -> list[Corp]:
        """전체 고유번호 목록 (상장·비상장 포함)."""
        return parse_corp_codes(self._get_zip("/corpCode.xml", {}))

    def iter_filings(
        self,
        corp_code: str | None,
        start: date,
        end: date,
        *,
        pblntf_ty: str | None = "A",
        final_only: bool = True,
    ) -> Iterator[Filing]:
        """공시 목록. pblntf_ty A=정기공시, B=주요사항보고, D=지분공시, I=거래소공시.

        final_only=True 이면 정정 전 원본을 빼고 최종 보고서만 받는다.
        corp_code 가 None 이면 전체 회사 (OpenDART 제한으로 조회 기간 3개월 이내).
        """
        page = 1
        while True:
            params = {
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "last_reprt_at": "Y" if final_only else "N",
                "page_no": page,
                "page_count": 100,
            }
            if corp_code:
                params["corp_code"] = corp_code
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            data = self._get_json("/list.json", params)
            if data is None:
                return
            for item in data.get("list", []):
                yield Filing.model_validate({**item, "rcept_dt": _parse_yyyymmdd(item["rcept_dt"])})
            if page >= int(data.get("total_page", 1)):
                return
            page += 1

    def document(self, rcept_no: str) -> bytes:
        """공시 원문 zip (XML 파일들)."""
        return self._get_zip("/document.xml", {"rcept_no": rcept_no})

    def financial_statements(
        self, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[FinancialRow]:
        """단일회사 전체 재무제표. fs_div CFS=연결, OFS=별도."""
        data = self._get_json(
            "/fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(bsns_year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        if data is None:
            return []
        return [FinancialRow.model_validate(row) for row in data.get("list", [])]


def parse_corp_codes(zip_bytes: bytes) -> list[Corp]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml = zf.read(zf.namelist()[0])
    root = etree.fromstring(xml)
    corps = []
    for node in root.iter("list"):

        def text(tag: str, node=node) -> str | None:
            value = (node.findtext(tag) or "").strip()
            return value or None

        modify = text("modify_date")
        corps.append(
            Corp(
                corp_code=text("corp_code"),
                corp_name=text("corp_name"),
                corp_eng_name=text("corp_eng_name"),
                stock_code=text("stock_code"),
                modify_date=_parse_yyyymmdd(modify) if modify else None,
            )
        )
    return corps


def _parse_yyyymmdd(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _error_status(resp: httpx.Response) -> str | None:
    if resp.content.startswith(b"PK"):
        return None
    return _parse_error_body(resp)[0]


def _parse_error_body(resp: httpx.Response) -> tuple[str, str]:
    """오류 응답은 JSON 또는 XML 로 온다."""
    try:
        data = resp.json()
        return data.get("status", "900"), data.get("message", "")
    except ValueError:
        pass
    try:
        root = etree.fromstring(resp.content)
        return root.findtext("status") or "900", root.findtext("message") or ""
    except etree.XMLSyntaxError:
        return "900", resp.text[:200]
