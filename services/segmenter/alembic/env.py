import os

from alembic import context
from sqlalchemy import create_engine

SCHEMA = "segmenter"


def run_migrations() -> None:
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
