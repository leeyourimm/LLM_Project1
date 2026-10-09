"""기업 대시보드 응답 미리 만들기와 캐시.

대시보드(/api/company/{stock}, /api/company/{stock}/quarters)는 재무 추이 계산, 최근 공시,
데이터 검증 결과를 모아 만든다. 사용자와 관계없는 부분을 회사별로 저장해 두고 다시 쓴다
(관심 종목 여부는 사용자마다 달라 요청할 때 따로 붙인다).

- 저장소: Redis 가 있으면 Redis (API 서버와 작업자가 같이 본다), 없으면 이 프로세스 메모리
- 키: 회사 + 회사 데이터 버전(company_versions) + 응답 형식 버전 + 요청 범위 + 날짜.
  재무 수치, 주요 공시, 검증 결과가 바뀌면 그 회사의 버전이 올라 예전 응답은 저절로 쓰이지 않는다.
  최근 공시 목록은 '오늘부터 며칠 전'이 기준이라 날이 바뀌어도 새로 만든다.
- 작업자: 새 정기보고서를 처리한 회사와 관심 종목 회사의 대시보드를 미리 만들어 둔다
  (worker/jobs.py 의 warm_dashboards). 화면이 기본으로 부르는 범위(DEFAULT_*)만 만든다.
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from datetime import date, timedelta

from dartrag.obs import metrics

log = logging.getLogger(__name__)

# 응답 모양이나 계산 방식을 바꾸면 올린다 (배포 전 응답을 쓰지 않게)
FORMAT_VERSION = 1
DEFAULT_YEARS = 5
DEFAULT_DAYS = 90
DEFAULT_QUARTERS = 12
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
QUARTER_NOTE = "4분기는 연간 금액에서 3분기 누적 금액을 빼서 계산합니다 (derived)."


def company_body(repo, corp: tuple[str, str, str], years: int, days: int, today: date) -> dict:
    """대시보드 응답 중 사용자와 관계없는 부분 (관심 종목 여부, 고지 문구는 API 가 붙인다)."""
    from dartrag.finance.series import company_series

    code, name, stock_code = corp
    series = company_series(repo, code, years)
    disclosures = repo.recent_disclosures(today - timedelta(days=days), 1, [code])
    issues = repo.data_issues(code)
    return {
        "corp_code": code,
        "corp_name": name,
        "stock_code": stock_code,
        "series": [asdict(p) for p in series],
        "disclosures": [d | {"url": DART_URL.format(d["rcept_no"])} for d in disclosures[:30]],
        # 재무 데이터 검증에 걸린 항목: 화면에 "확인 필요"로 보여 준다
        "issues": [
            {k: i[k] for k in ("bsns_year", "reprt_code", "fs_div", "rule", "severity", "detail")}
            for i in issues
        ],
    }


def quarters_body(repo, corp: tuple[str, str, str], count: int) -> dict:
    from dartrag.finance.series import quarterly_series

    code, name, stock_code = corp
    points = quarterly_series(repo, code, count)
    return {
        "corp_code": code,
        "corp_name": name,
        "stock_code": stock_code,
        "quarters": [asdict(p) | {"label": p.label} for p in points],
        "note": QUARTER_NOTE,
    }


class MemoryStore:
    """Redis 가 없을 때 쓰는 프로세스 안 저장소 (오래 안 쓴 것부터 버린다)."""

    def __init__(self, max_items: int = 256, clock=time.monotonic):
        self.max_items = max_items
        self.clock = clock
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item[0] <= self.clock():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return item[1]

    def set(self, key: str, value: str, ex: int) -> None:
        with self._lock:
            self._items[key] = (self.clock() + ex, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class DashboardCache:
    """회사 데이터 버전을 키에 넣은 대시보드 응답 캐시.

    Redis 가 없거나 잠시 안 되면 이 프로세스 메모리에 둔다. 캐시가 고장 나도 화면은 직접 만들어
    계속 보여 준다."""

    def __init__(self, redis=None, *, ttl: int = 6 * 3600, memory: MemoryStore | None = None):
        self.redis = redis
        self.memory = memory or MemoryStore()
        self.ttl = ttl

    @staticmethod
    def key(kind: str, corp_code: str, version: str, **params) -> str:
        scope = ":".join(f"{k}={params[k]}" for k in sorted(params))
        return f"dartrag:dashboard:v{FORMAT_VERSION}:{kind}:{corp_code}:{version}:{scope}"

    def _redis(self, op: str, kind: str, *args):
        """Redis 명령 → (성공 여부, 결과). 실패하면 부른 쪽이 메모리 저장소를 쓴다."""
        try:
            return True, getattr(self.redis, op)(*args)
        except Exception as e:  # noqa: BLE001 - 캐시가 고장 나도 대시보드는 나가야 한다
            log.warning("대시보드 캐시(Redis) %s 실패: %s", op, type(e).__name__)
            metrics.DASHBOARD_CACHE.labels(kind, "error").inc()
            return False, None

    def _get(self, kind: str, key: str) -> dict | None:
        ok, raw = self._redis("get", kind, key) if self.redis is not None else (False, None)
        if not ok:
            raw = self.memory.get(key)
        return json.loads(raw) if raw else None

    def _put(self, kind: str, key: str, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False, default=str)
        if self.redis is None or not self._redis("set", kind, key, raw, self.ttl)[0]:
            self.memory.set(key, raw, ex=self.ttl)

    def fetch(self, kind: str, key: str, build) -> dict:
        """있으면 꺼내고 없으면 build() 로 만들어 저장한다. 지표에 hit/miss 를 센다."""
        cached = self._get(kind, key)
        if cached is not None:
            metrics.DASHBOARD_CACHE.labels(kind, "hit").inc()
            return cached
        metrics.DASHBOARD_CACHE.labels(kind, "miss").inc()
        started = time.perf_counter()
        body = build()
        metrics.DASHBOARD_BUILD_SECONDS.labels(kind).observe(time.perf_counter() - started)
        self._put(kind, key, body)
        # 저장했다가 다시 읽은 것과 같은 모양(날짜는 문자열)으로 돌려준다
        return json.loads(json.dumps(body, ensure_ascii=False, default=str))

    # --- 화면별 ----------------------------------------------------------------

    def company(
        self,
        repo,
        corp: tuple[str, str, str],
        years: int = DEFAULT_YEARS,
        days: int = DEFAULT_DAYS,
        today: date | None = None,
    ) -> dict:
        today = today or date.today()
        key = self.key(
            "company", corp[0], repo.company_version(corp[0]), years=years, days=days, day=today
        )
        return self.fetch("company", key, lambda: company_body(repo, corp, years, days, today))

    def quarters(self, repo, corp: tuple[str, str, str], count: int = DEFAULT_QUARTERS) -> dict:
        key = self.key("quarters", corp[0], repo.company_version(corp[0]), count=count)
        return self.fetch("quarters", key, lambda: quarters_body(repo, corp, count))

    def warm(self, repo, corp: tuple[str, str, str], today: date | None = None) -> bool:
        """화면이 처음 부르는 범위의 대시보드를 미리 만든다. 이미 최신이면 False."""
        today = today or date.today()
        version = repo.company_version(corp[0])
        keys = {
            "company": self.key(
                "company", corp[0], version, years=DEFAULT_YEARS, days=DEFAULT_DAYS, day=today
            ),
            "quarters": self.key("quarters", corp[0], version, count=DEFAULT_QUARTERS),
        }
        if all(self._get(kind, k) is not None for kind, k in keys.items()):
            return False
        body = company_body(repo, corp, DEFAULT_YEARS, DEFAULT_DAYS, today)
        self._put("company", keys["company"], body)
        self._put("quarters", keys["quarters"], quarters_body(repo, corp, DEFAULT_QUARTERS))
        return True


def warm_companies(cache: DashboardCache, repo, companies, today: date | None = None) -> dict:
    """여러 회사의 대시보드를 미리 만든다. 한 회사가 실패해도 나머지는 계속한다."""
    built, fresh, errors = 0, 0, []
    for corp in companies:
        try:
            if cache.warm(repo, corp, today):
                built += 1
            else:
                fresh += 1
        except Exception as e:  # noqa: BLE001 - 한 회사 때문에 전체가 멈추면 안 된다
            repo.conn.rollback()
            errors.append(f"{corp[0]}: {type(e).__name__}: {e}")
            log.warning("대시보드 미리 만들기 실패 %s: %s", corp[0], e)
    return {"built": built, "fresh": fresh, "errors": errors}
