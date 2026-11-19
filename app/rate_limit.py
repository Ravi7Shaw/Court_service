from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from app.redis_support import RedisError

from app.errors import ApiError

log = logging.getLogger(__name__)
_local_lock = threading.Lock()
_local_events: dict[str, deque[float]] = defaultdict(deque)


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int, *, per_ip: bool = True) -> None:
    settings = request.app.state.settings
    client = request.app.state.redis
    # Nginx overwrites this header after resolving the trusted ALB client address.
    # Uvicorn trusts that proxy on the private API port; the limiter ignores raw headers.
    ip = request.client.host if request.client else "unknown"
    key = f"ratelimit:{bucket}:{ip}" if per_ip else f"ratelimit:{bucket}"
    try:
        count = int(client.eval(
            "local n = redis.call('INCR', KEYS[1]); "
            "if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; return n",
            1, key, window_seconds,
        ))
        if count > limit:
            raise ApiError("rate_limited", 429, "too many requests; try again later")
    except ApiError:
        raise
    except RedisError as exc:
        if settings.environment == "production":
            log.exception("Redis rate limiter unavailable")
            raise ApiError("temporarily_unavailable", 503, "authentication service temporarily unavailable") from exc
        now = time.monotonic()
        with _local_lock:
            events = _local_events[key]
            while events and events[0] < now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                raise ApiError("rate_limited", 429, "too many requests; try again later")
            events.append(now)
