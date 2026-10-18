import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine

DEFAULT_URL = "postgresql+psycopg://recording:recording-local@localhost:5432/risk_segmenter"

os.environ.setdefault("PUBSUB_EMULATOR_HOST", "localhost:8085")
os.environ.setdefault("PUBSUB_PROJECT_ID", "risk-segmenter-local")


@pytest.fixture
def database_url() -> str:
    return os.environ.get("RECORDING_DATABASE_URL", DEFAULT_URL)


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        connection.begin()
        yield connection
        connection.rollback()
