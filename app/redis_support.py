"""Redis imports with a development-only fallback for source-level test environments."""
from __future__ import annotations

try:
    from redis import Redis as Redis
    from redis.exceptions import RedisError as RedisError
except ImportError:  # pragma: no cover - exercised only when optional local tooling is incomplete
    class RedisError(Exception):
        pass

    class Redis:  # type: ignore[no-redef]
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        def _missing(self):
            raise RedisError("redis package is not installed")

        def ping(self):
            return self._missing()

        def incr(self, _key):
            return self._missing()

        def expire(self, _key, _seconds):
            return self._missing()

        def eval(self, *_args):
            return self._missing()

        def get(self, _key):
            return self._missing()

        def setex(self, _key, _seconds, _value):
            return self._missing()

        def delete(self, *_keys):
            return self._missing()

        def close(self):
            return None
