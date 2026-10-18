import argparse
import logging
import os

from google.cloud import pubsub_v1

from notifier.adapters.dead_letter_pubsub import PubSubDeadLetters, PubSubSourceTopic
from notifier.entrypoints.worker import SUBSCRIPTION
from notifier.service_layer.replay import replay

PULL_BATCH = 100
PULL_TIMEOUT = 5.0
ACK_TIMEOUT = 30.0

logger = logging.getLogger("notifier.replay")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Republish a subscription's dead-lettered messages to its topic (#20, #35)."
    )
    parser.add_argument("--subscription", required=True, choices=[SUBSCRIPTION])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    project_id = os.environ["PUBSUB_PROJECT_ID"]
    subscriber = pubsub_v1.SubscriberClient()
    publisher = pubsub_v1.PublisherClient(
        publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
    )
    topic = subscriber.get_subscription(
        subscription=subscriber.subscription_path(project_id, args.subscription)
    ).topic
    dead_letter = subscriber.subscription_path(project_id, f"{args.subscription}-dead-letter")

    result = replay(
        PubSubDeadLetters(subscriber, dead_letter, PULL_BATCH, PULL_TIMEOUT),
        PubSubSourceTopic(publisher, topic, ACK_TIMEOUT),
    )
    logger.info("replayed %d message(s) from %s to %s", result.count, dead_letter, topic)
    if result.parked_again:
        logger.error("a replayed message was dead-lettered again; replay stopped")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
