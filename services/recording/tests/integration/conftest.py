import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine

DEFAULT_URL = "postgresql+psycopg://recording:recording-local@localhost:5432/risk_segmenter"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(os.environ.get("RECORDING_DATABASE_URL", DEFAULT_URL))
    yield engine
    engine.dispose()
