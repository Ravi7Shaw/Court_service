from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql+psycopg://booking:booking@localhost:5432/booking")


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
