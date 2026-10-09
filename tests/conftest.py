import io
import zipfile

import pytest


def make_zip(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
    <corp_eng_name>SAMSUNG ELECTRONICS CO,.LTD</corp_eng_name>
    <stock_code>005930</stock_code><modify_date>20240101</modify_date></list>
  <list><corp_code>00164779</corp_code><corp_name>에스케이하이닉스</corp_name>
    <corp_eng_name>SK hynix Inc.</corp_eng_name>
    <stock_code>000660</stock_code><modify_date>20240101</modify_date></list>
  <list><corp_code>99999999</corp_code><corp_name>비상장회사</corp_name>
    <stock_code> </stock_code><modify_date>20240101</modify_date></list>
</result>""".encode()


@pytest.fixture
def corp_zip() -> bytes:
    return make_zip("CORPCODE.xml", CORP_XML)
