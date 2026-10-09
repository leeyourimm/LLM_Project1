"""OpenDART 호출 수와 간격을 여러 작업자가 함께 지키도록 Redis 로 센다.

OpenDART 는 인증키 하나에 하루 20,000회까지 허용한다. 과거 데이터 채우기(backfill)가
한도를 다 써서 새 공시 처리가 막히지 않게, 작업마다 쓸 수 있는 몫을 나눈다.
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dartrag.dart.client import QuotaExceeded

KST = ZoneInfo("Asia/Seoul")
DAILY_LIMIT = 20_000


class DailyQuota:
    def __init__(self, redis, limit: int, *, prefix: str = "dartrag:dart-quota", now=None):
        self.redis = redis
        self.limit = limit
        self.prefix = prefix
        self._now = now or (lambda: datetime.now(KST))

    def _key(self) -> str:
        # 한도는 한국 시간 자정에 초기화된다
        return f"{self.prefix}:{self._now():%Y%m%d}"

    def used(self) -> int:
        return int(self.redis.get(self._key()) or 0)

    def remaining(self) -> int:
        return max(self.limit - self.used(), 0)

    def use(self, n: int = 1) -> None:
        key = self._key()
        used = self.redis.incrby(key, n)
        if used == n:
            self.redis.expire(key, 2 * 86400)
        if used > self.limit:
            raise QuotaExceeded(used, self.limit)


class SharedThrottle:
    """작업자 여러 개가 같은 키로 부를 때도 호출 간격을 지킨다 (분당 요청 제한 대비)."""

    def __init__(self, redis, min_interval: float, *, key="dartrag:dart-last", sleep=time.sleep):
        self.redis = redis
        self.min_interval = min_interval
        self.key = key
        self.sleep = sleep

    def wait(self) -> None:
        ms = int(self.min_interval * 1000)
        # SET NX PX: 이 간격 안에 다른 호출이 없을 때만 자리를 얻는다
        while not self.redis.set(self.key, "1", nx=True, px=ms):
            ttl = self.redis.pttl(self.key)
            self.sleep(max(ttl, 10) / 1000)
