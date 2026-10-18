# /// script
# requires-python = ">=3.14"
# dependencies = ["google-cloud-pubsub>=2.43.0"]
# ///
import contextlib
import os
from dataclasses import dataclass

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1
from google.protobuf import duration_pb2

MAX_DELIVERY_ATTEMPTS = 5
DEAD_LETTER_RETENTION = duration_pb2.Duration(seconds=7 * 24 * 3600)


@dataclass(frozen=True)
class SubscriptionSpec:
    topic: str
    name: str


TOPICS = ["activity-recorded", "segmenter-events"]
SUBSCRIPTIONS = [
    SubscriptionSpec(topic="activity-recorded", name="segmenter-activity-recorded"),
    SubscriptionSpec(topic="segmenter-events", name="notifier-segmenter-events"),
]


def main() -> None:
    project_id = os.environ["PUBSUB_PROJECT_ID"]
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    for topic in TOPICS:
        _create_topic(publisher, publisher.topic_path(project_id, topic))

    for spec in SUBSCRIPTIONS:
        dead_letter = f"{spec.name}-dead-letter"
        dead_letter_topic = publisher.topic_path(project_id, dead_letter)
        _create_topic(publisher, dead_letter_topic)
        _create_subscription(
            subscriber,
            {
                "name": subscriber.subscription_path(project_id, dead_letter),
                "topic": dead_letter_topic,
                "message_retention_duration": DEAD_LETTER_RETENTION,
            },
        )
        _create_subscription(
            subscriber,
            {
                "name": subscriber.subscription_path(project_id, spec.name),
                "topic": publisher.topic_path(project_id, spec.topic),
                "enable_message_ordering": True,
                "dead_letter_policy": {
                    "dead_letter_topic": dead_letter_topic,
                    "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
                },
            },
        )
    print(f"pubsub topology ready in {project_id}")


def _create_topic(publisher: pubsub_v1.PublisherClient, name: str) -> None:
    with contextlib.suppress(AlreadyExists):
        publisher.create_topic(name=name)


def _create_subscription(
    subscriber: pubsub_v1.SubscriberClient, request: dict[str, object]
) -> None:
    with contextlib.suppress(AlreadyExists):
        subscriber.create_subscription(request=request)


if __name__ == "__main__":
    main()
