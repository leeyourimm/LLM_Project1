from datetime import date

from pydantic import BaseModel


class Corp(BaseModel):
    corp_code: str
    corp_name: str
    corp_eng_name: str | None = None
    stock_code: str | None = None
    modify_date: date | None = None

    @property
    def is_listed(self) -> bool:
        return bool(self.stock_code)


class Filing(BaseModel):
    """list.json 의 공시 한 건."""

    corp_code: str
    corp_name: str
    stock_code: str | None = None
    corp_cls: str | None = None
    report_nm: str
    rcept_no: str
    flr_nm: str | None = None
    rcept_dt: date
    rm: str | None = None


class FinancialRow(BaseModel):
    """fnlttSinglAcntAll.json 의 계정 한 줄 (원본 문자열 그대로)."""

    rcept_no: str
    reprt_code: str
    bsns_year: str
    corp_code: str
    sj_div: str
    sj_nm: str
    account_id: str | None = None
    account_nm: str
    account_detail: str | None = None
    thstrm_nm: str | None = None
    thstrm_amount: str | None = None
    thstrm_add_amount: str | None = None
    frmtrm_nm: str | None = None
    frmtrm_amount: str | None = None
    ord: str | None = None
    currency: str | None = None
