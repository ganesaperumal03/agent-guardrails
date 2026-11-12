"""
Alembic's entry point. Alembic runs this file every time you use an `alembic` command.

It answers two questions for Alembic:
  1. Which database?          -> SQLALCHEMY_URL from app/config.py (same as the app uses)
  2. What should tables look like? -> Base.metadata from app/models.py
     (only needed for `--autogenerate`, which compares the models with the real database)
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import config
from app.models import Base

alembic_config = context.config

# Set up logging from alembic.ini (keep the app's own loggers alive)
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline mode (`alembic upgrade head --sql`): print the SQL, don't connect."""
    context.configure(url=config.SQLALCHEMY_URL, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Normal mode: connect to the database and run the migrations."""
    engine = create_engine(config.SQLALCHEMY_URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
