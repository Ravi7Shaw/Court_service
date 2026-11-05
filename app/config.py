from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    staff_key: str
    pool_size: int
    max_overflow: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql+psycopg://booking:booking@localhost:5432/booking"
            ),
            staff_key=os.environ.get("STAFF_KEY", "dev-staff-key"),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        )
