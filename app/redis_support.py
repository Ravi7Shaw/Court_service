"""Shared Redis dependency; connection outages are handled by callers."""
from redis import Redis as Redis
from redis.exceptions import RedisError as RedisError
