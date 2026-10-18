import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from segmenter.adapters.database import engine_from_env
from segmenter.adapters.outbox import SqlOutbox
from segmenter.adapters.pubsub_publisher import PubSubPublisher
from segmenter.service_layer.relay import Publisher, RelayResult, clean_outbox, relay_once

IDLE_WAIT = 0.5
MAX_BACKOFF = 30.0
CLEANUP_EVERY = timedelta(hours=1)

logger = logging.getLogger("segmenter.relay")


def run(engine: Engine, publisher: Publisher) -> None:
    backoff = IDLE_WAIT
    next_cleanup = datetime.now(UTC)
    while True:
        try:
            result = _relay(engine, publisher)
            next_cleanup = _clean_if_due(engine, next_cleanup)
        except Exception:
            logger.exception("relay iteration failed")
            result = RelayResult(sent=0, failed=True)
        if result.failed:
            backoff = min(backoff * 2, MAX_BACKOFF)
        else:
            backoff = IDLE_WAIT
            if result.sent:
                continue
        time.sleep(backoff)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(engine_from_env(), PubSubPublisher.from_env())


def _relay(engine: Engine, publisher: Publisher) -> RelayResult:
    with engine.begin() as connection:
        result = relay_once(SqlOutbox(connection), publisher, datetime.now(UTC))
    if result.sent:
        logger.info("published %d outbox rows", result.sent)
    return result


def _clean_if_due(engine: Engine, due: datetime) -> datetime:
    if datetime.now(UTC) < due:
        return due
    with engine.begin() as connection:
        deleted = clean_outbox(SqlOutbox(connection), datetime.now(UTC))
    logger.info("deleted %d sent outbox rows", deleted)
    return datetime.now(UTC) + CLEANUP_EVERY


if __name__ == "__main__":
    main()
