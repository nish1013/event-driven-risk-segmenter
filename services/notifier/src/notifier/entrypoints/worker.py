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

from notifier.adapters.alert_log import StructuredAlertLog
from notifier.adapters.database import engine_from_env
from notifier.adapters.processed_events import SqlProcessedEvents
from notifier.domain.events import SegmenterEvent
from notifier.entrypoints.messages import parse
from notifier.service_layer.cleanup import clean_processed_events
from notifier.service_layer.on_segmenter_event import on_segmenter_event
from notifier.service_layer.unit_of_work import SqlUnitOfWork

SUBSCRIPTION = "notifier-segmenter-events"
MAX_IN_FLIGHT = 10
CLEANUP_EVERY = timedelta(hours=1)
RESUBSCRIBE_WAIT = 5.0

logger = logging.getLogger("notifier.worker")


class Message(Protocol):
    data: bytes

    def ack(self) -> None: ...

    def nack(self) -> None: ...


def handle(message: Message, process: Callable[[SegmenterEvent], object]) -> None:
    # Ack only after commit. A nack sends the message back; after 5 attempts it is dead-lettered.
    try:
        event = parse(message.data)
    except ValidationError:
        logger.error("unreadable segmenter event, nacked", exc_info=True)
        message.nack()
        return
    if event is None:
        message.ack()
        return
    try:
        process(event)
    except Exception:
        logger.exception("segmenter event %s not applied, nacked", event.event_id)
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


def _processor(engine: Engine) -> Callable[[SegmenterEvent], object]:
    alert_log = StructuredAlertLog(logging.getLogger("notifier.alerts"))

    def process(event: SegmenterEvent) -> object:
        return on_segmenter_event(
            event,
            lambda: SqlUnitOfWork(engine),
            alert_log,
            lambda: datetime.now(UTC),
            uuid4,
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
