import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from google.cloud import pubsub_v1
from pydantic import ValidationError
from sqlalchemy import Engine

from segmenter.adapters.database import engine_from_env
from segmenter.adapters.processed_events import SqlProcessedEvents
from segmenter.domain.policy import POLICY_V1
from segmenter.entrypoints.messages import ActivityRecordedMessage
from segmenter.service_layer.cleanup import clean_processed_events
from segmenter.service_layer.on_activity_recorded import ActivityRecorded, on_activity_recorded
from segmenter.service_layer.unit_of_work import SqlUnitOfWork

SUBSCRIPTION = "segmenter-activity-recorded"
MAX_IN_FLIGHT = 10
CLEANUP_EVERY = timedelta(hours=1)
RESUBSCRIBE_WAIT = 5.0

logger = logging.getLogger("segmenter.worker")


class Message(Protocol):
    data: bytes

    def ack(self) -> None: ...

    def nack(self) -> None: ...


def handle(message: Message, process: Callable[[ActivityRecorded], object]) -> None:
    # Ack only after commit. A nack sends the message back; after 5 attempts it is dead-lettered.
    try:
        event = ActivityRecordedMessage.model_validate_json(message.data).to_event()
    except ValidationError:
        logger.error("unreadable ActivityRecorded, nacked", exc_info=True)
        message.nack()
        return
    try:
        process(event)
    except Exception:
        logger.exception("ActivityRecorded %s not applied, nacked", event.event_id)
        message.nack()
        return
    message.ack()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = engine_from_env()
    threading.Thread(target=_clean_forever, args=(engine,), daemon=True).start()
    subscriber = pubsub_v1.SubscriberClient()
    subscription = subscriber.subscription_path(os.environ["PUBSUB_PROJECT_ID"], SUBSCRIPTION)
    process = _processor(engine)
    while True:
        future = subscriber.subscribe(
            subscription,
            callback=lambda message: handle(message, process),
            flow_control=pubsub_v1.types.FlowControl(max_messages=MAX_IN_FLIGHT),
        )
        logger.info("listening on %s", subscription)
        try:
            future.result()
        except Exception:
            logger.exception("subscription stopped, resubscribing")
            future.cancel()
            time.sleep(RESUBSCRIBE_WAIT)


def _processor(engine: Engine) -> Callable[[ActivityRecorded], object]:
    def process(event: ActivityRecorded) -> object:
        return on_activity_recorded(
            event,
            lambda: SqlUnitOfWork(engine),
            POLICY_V1,
            lambda: datetime.now(UTC),
            uuid4,
            time.sleep,
        )

    return process


def _clean_forever(engine: Engine) -> None:
    while True:
        try:
            with engine.begin() as connection:
                deleted = clean_processed_events(SqlProcessedEvents(connection), datetime.now(UTC))
            logger.info("deleted %d processed events", deleted)
        except Exception:
            logger.exception("processed events cleanup failed")
        time.sleep(CLEANUP_EVERY.total_seconds())


if __name__ == "__main__":
    main()
