"""요청 한도. LLM 을 부르는 요청은 몇 개만 몰려도 다른 사람이 오래 기다리게 된다.

사용자별(로그인하지 않았으면 접속 주소별)로 구간마다 횟수를 센다. Redis 가 있으면
API 서버 여러 개가 함께 세고, 없으면 서버 안에서만 센다.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

from dartrag.obs import metrics


@dataclass(frozen=True)
class Rule:
    limit: int
    seconds: int
    label: str  # 안내 문구용, 예: "1분에 6번"


class RateLimiter:
    def __init__(self, redis=None, *, prefix: str = "dartrag:rate", now=time.time):
        self.redis = redis
        self.prefix = prefix
        self._now = now
        self._local: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _incr(self, key: str, seconds: int) -> tuple[int, float]:
        """(이번 구간 횟수, 구간이 끝나기까지 남은 초)."""
        now = self._now()
        window = int(now // seconds)
        reset = (window + 1) * seconds - now
        full = f"{self.prefix}:{key}:{seconds}:{window}"
        if self.redis is not None:
            try:
                count = self.redis.incr(full)
                if count == 1:
                    self.redis.expire(full, seconds + 5)
                return int(count), reset
            except Exception:  # noqa: BLE001 - Redis 가 잠깐 끊겨도 서비스는 계속
                pass
        with self._lock:
            if len(self._local) > 50_000:  # 지난 구간은 버린다
                self._local = {k: v for k, v in self._local.items() if v[1] > now}
            count, until = self._local.get(full, (0, now + reset))
            self._local[full] = (count + 1, until)
            return count + 1, reset

    def hit(self, scope: str, who: str, rules: list[Rule]) -> tuple[Rule, float] | None:
        """한도를 넘었으면 (걸린 규칙, 다시 시도할 수 있을 때까지 초), 아니면 None."""
        for rule in rules:
            if rule.limit <= 0:
                continue
            count, reset = self._incr(f"{scope}:{who}", rule.seconds)
            if count > rule.limit:
                return rule, reset
        return None


def client_key(request: Request, user) -> str:
    if user is not None:
        return f"u{user.id}"
    # 프록시 뒤에서는 uvicorn --proxy-headers 가 실제 접속 주소로 바꿔 둔다
    return f"ip{request.client.host if request.client else '-'}"


def make_dependency(
    limiter: RateLimiter,
    scope: str,
    rules: list[Rule],
    user_dep: Callable,
    when: Callable[[Request], bool] | None = None,
) -> Callable:
    """FastAPI 의존성. 라우트에 dependencies=[Depends(...)] 로 붙인다.

    user_dep 는 로그인하지 않았어도 오류 없이 None 을 돌려줘야 한다.
    when 이 있으면 그 조건일 때만 센다 (예: PDF 에 요약을 넣을 때만)."""
    from typing import Annotated

    from fastapi import Depends

    def check(request: Request, user: Annotated[object, Depends(user_dep)] = None) -> None:
        if when is not None and not when(request):
            return
        hit = limiter.hit(scope, client_key(request, user), rules)
        if hit is None:
            return
        rule, reset = hit
        metrics.RATE_LIMITED.labels(scope).inc()
        retry = max(int(reset) + 1, 1)
        raise HTTPException(
            429,
            f"요청이 많습니다 ({rule.label}까지). {retry}초 뒤에 다시 시도해 주세요.",
            headers={"Retry-After": str(retry)},
        )

    return check
