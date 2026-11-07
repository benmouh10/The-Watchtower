"""
GlobeLens AI — Alembic Migration Environment
=============================================
This file is executed by Alembic before running any migration command.

Key responsibilities:
1. Wire DATABASE_URL from environment (via settings.SYNC_DATABASE_URL).
2. Import Base.metadata so Alembic can diff models vs. the live DB.
3. Run migrations in "offline" mode (SQL scripts) or "online" mode (live DB).
4. Register the pgvector type so Alembic doesn't choke on Vector columns.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from alembic import context

# ─────────────────────────────────────────────────────────────────────────────
# Path Setup
# ─────────────────────────────────────────────────────────────────────────────
# Ensure that `from app.xxx import yyy` works inside env.py.
# When Alembic runs, the CWD is backend/, so we insert backend/ into sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────────────────────
# Application Imports
# ─────────────────────────────────────────────────────────────────────────────
# Import settings BEFORE models so DATABASE_URL is resolved first.
from src.core.config import settings  # noqa: E402

# Import ALL entity models so that Base.metadata contains every table.
# Even if a model is not directly used here, it must be imported to register
# its table with the metadata registry.
from src.data.entities.models import Base  # noqa: E402  # registers all tables
# Explicit imports ensure models are in-scope (avoids "no changes" autogenerate)
import src.data.entities.models  # noqa: F401, E402

# Register pgvector's custom type with SQLAlchemy's type registry so Alembic
# can handle Vector columns without raising CompileError.
from pgvector.sqlalchemy import Vector  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Alembic Config Object
# ─────────────────────────────────────────────────────────────────────────────
config = context.config

# Inject the sync DATABASE_URL from our settings — overrides the placeholder
# value in alembic.ini so we never hardcode credentials in config files.
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tell Alembic which metadata to diff against the live database.
# This is what enables --autogenerate to detect added/removed/modified tables.
target_metadata = Base.metadata


# ─────────────────────────────────────────────────────────────────────────────
# Offline Mode — generate SQL script without a live DB connection
# ─────────────────────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    Generates a SQL script instead of connecting to the database.
    Useful for reviewing what SQL will be executed before applying it.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include schemas and render as_uuid for PostgreSQL UUID columns
        include_schemas=False,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ─────────────────────────────────────────────────────────────────────────────
# Online Mode — connect to the live DB and apply migrations
# ─────────────────────────────────────────────────────────────────────────────
def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.
    Connects to the live PostgreSQL database and applies pending migrations.
    Uses NullPool so Alembic doesn't hold connections open after migrations.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
