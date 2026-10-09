"""공시 제목으로 사건 종류와 중요도를 분류한다.

중요도 3: 주가·지분 구조에 직접 영향 (증자, 감자, 메자닌 발행, 합병·분할, 소송, 회생 등)
중요도 2: 눈여겨볼 사건 (자사주, 대형 계약, 배당 결정, 잠정 실적 등)
중요도 1: 그 외
"""

import re
from dataclasses import dataclass

_PREFIX_RE = re.compile(r"^(\s*\[[^\]]+\]\s*)+")


@dataclass(frozen=True)
class Event:
    type: str
    label: str
    importance: int
    correction: bool = False


# (패턴, 종류, 표시 이름, 중요도). 위에서부터 먼저 맞는 것을 쓴다
RULES: list[tuple[re.Pattern, str, str, int]] = [
    (re.compile(p), t, label, imp)
    for p, t, label, imp in [
        (r"유무상증자결정", "rights_bonus_issue", "유무상증자", 3),
        (r"유상증자결정", "rights_issue", "유상증자", 3),
        (r"무상증자결정", "bonus_issue", "무상증자", 2),
        (r"감자결정", "capital_reduction", "감자", 3),
        (r"전환사채권발행결정", "convertible_bond", "전환사채(CB) 발행", 3),
        (r"신주인수권부사채권발행결정", "bond_with_warrant", "신주인수권부사채(BW) 발행", 3),
        (r"교환사채권발행결정", "exchangeable_bond", "교환사채(EB) 발행", 2),
        (r"분할합병결정", "split_merger", "분할합병", 3),
        (r"합병결정", "merger", "합병", 3),
        (r"분할결정", "split", "회사분할", 3),
        (r"영업양수결정|영업양도결정", "business_transfer", "영업양수도", 3),
        (r"주식교환|주식이전", "share_exchange", "주식교환·이전", 3),
        (r"소송\s*등의\s*(제기|판결|신청)", "litigation", "소송", 3),
        (
            r"회생절차|파산신청|부도발생|은행거래정지|해산사유|채권은행\s*등의\s*관리절차",
            "distress",
            "부실·회생",
            3,
        ),
        (r"영업정지", "suspension", "영업정지", 3),
        (r"최대주주\s*변경", "largest_shareholder_change", "최대주주 변경", 3),
        (r"감사의견|상장폐지|관리종목|거래정지", "listing_risk", "상장 유지 위험", 3),
        (r"횡령|배임", "embezzlement", "횡령·배임", 3),
        (r"자기주식\s*(취득|처분|소각)|자기주식취득신탁|신탁계약", "treasury_stock", "자기주식", 2),
        (r"단일판매\s*[ㆍ·]?\s*공급계약", "supply_contract", "대형 공급계약", 2),
        (r"(현금\s*[ㆍ·]?\s*현물\s*)?배당\s*결정", "dividend", "배당 결정", 2),
        (r"영업\s*\(잠정\)\s*실적|잠정\s*실적", "preliminary_earnings", "잠정 실적", 2),
        (
            r"타법인\s*주식\s*및\s*출자증권\s*(양수|양도|취득|처분)",
            "equity_investment",
            "타법인 지분 거래",
            2,
        ),
        (r"유형자산\s*(양수|양도|취득|처분)", "asset_transfer", "유형자산 거래", 2),
        (r"신규\s*시설\s*투자", "capex", "신규 시설투자", 2),
        (r"주식\s*분할|주식\s*병합", "stock_split", "주식 분할·병합", 2),
        (r"대량보유상황보고", "major_holding", "5% 대량보유 보고", 1),
        (r"임원\s*[ㆍ·]?\s*주요주주\s*특정증권", "insider_holding", "임원·주요주주 지분 변동", 1),
    ]
]


def classify(report_nm: str) -> Event:
    correction = bool(_PREFIX_RE.match(report_nm)) and "정정" in report_nm
    name = _PREFIX_RE.sub("", report_nm)
    for pattern, type_, label, importance in RULES:
        if pattern.search(name):
            return Event(type_, label, importance, correction)
    return Event("other", "기타", 1, correction)
