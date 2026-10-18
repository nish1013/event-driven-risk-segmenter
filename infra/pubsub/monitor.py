# /// script
# requires-python = ">=3.14"
# dependencies = ["google-cloud-pubsub>=2.43.0"]
# ///
import logging
import os
import time
from collections.abc import Iterable

from google.api_core.exceptions import DeadlineExceeded
from google.cloud import pubsub_v1
from init import SUBSCRIPTIONS

PULL_BATCH = 100
PULL_TIMEOUT = 2.0

logger = logging.getLogger("dead-letter-monitor")


def check(subscriber: pubsub_v1.SubscriberClient, subscriptions: Iterable[str]) -> dict[str, int]:
    depths = {subscription: depth(subscriber, subscription) for subscription in subscriptions}
    for subscription, found in depths.items():
        if found > 0:
            logger.error("%s holds %d dead-lettered message(s)", subscription, found)
    return depths


def depth(subscriber: pubsub_v1.SubscriberClient, subscription: str) -> int:
    # The emulator has no backlog metric (#37 names Cloud Monitoring for v2), so the depth is
    # read by pulling every message and releasing it at once. Nothing is acked.
    ack_ids: list[str] = []
    try:
        while batch := _pull(subscriber, subscription):
            ack_ids += batch
    finally:
        if ack_ids:
            subscriber.modify_ack_deadline(
                subscription=subscription, ack_ids=ack_ids, ack_deadline_seconds=0
            )
    return len(ack_ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    project_id = os.environ["PUBSUB_PROJECT_ID"]
    every = float(os.environ.get("DEAD_LETTER_CHECK_EVERY_S", "60"))
    subscriber = pubsub_v1.SubscriberClient()
    subscriptions = [
        subscriber.subscription_path(project_id, spec.dead_letter) for spec in SUBSCRIPTIONS
    ]
    logger.info("checking %s every %ss", ", ".join(subscriptions), every)
    while True:
        try:
            check(subscriber, subscriptions)
        except Exception:
            logger.exception("dead-letter check failed")
        time.sleep(every)


def _pull(subscriber: pubsub_v1.SubscriberClient, subscription: str) -> list[str]:
    try:
        response = subscriber.pull(
            subscription=subscription, max_messages=PULL_BATCH, timeout=PULL_TIMEOUT
        )
    except DeadlineExceeded:
        return []
    return [received.ack_id for received in response.received_messages]


if __name__ == "__main__":
    main()
