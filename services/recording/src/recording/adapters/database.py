import os

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


class PostgresProbe:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def is_reachable(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True


def engine_from_env() -> Engine:
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
