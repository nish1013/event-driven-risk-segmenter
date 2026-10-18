import logging
import uuid
from collections.abc import Iterator

import monitor
import pytest
from google.cloud import pubsub_v1
from stack import PROJECT_ID


@pytest.fixture
def dead_letter() -> Iterator[tuple[pubsub_v1.PublisherClient, str, str]]:
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    suffix = uuid.uuid4().hex[:8]
    topic = publisher.topic_path(PROJECT_ID, f"monitor-{suffix}-dead-letter")
    subscription = subscriber.subscription_path(PROJECT_ID, f"monitor-{suffix}-dead-letter")
    publisher.create_topic(name=topic)
    subscriber.create_subscription(name=subscription, topic=topic)
    yield publisher, topic, subscription
    subscriber.delete_subscription(subscription=subscription)
    publisher.delete_topic(topic=topic)
    subscriber.close()


def test_37_depth_above_zero_logged_at_error(
    dead_letter: tuple[pubsub_v1.PublisherClient, str, str], caplog: pytest.LogCaptureFixture
) -> None:
    publisher, topic, subscription = dead_letter
    for data in (b"one", b"two"):
        publisher.publish(topic, data).result(timeout=30)

    with pubsub_v1.SubscriberClient() as subscriber, caplog.at_level(logging.ERROR):
        first = monitor.check(subscriber, [subscription])
        second = monitor.check(subscriber, [subscription])

    assert first == second == {subscription: 2}, "a check reads the depth and consumes nothing"
    assert [r.getMessage() for r in caplog.records] == [
        f"{subscription} holds 2 dead-lettered message(s)"
    ] * 2


def test_37_empty_dead_letter_logs_nothing(
    dead_letter: tuple[pubsub_v1.PublisherClient, str, str], caplog: pytest.LogCaptureFixture
) -> None:
    _, _, subscription = dead_letter

    with pubsub_v1.SubscriberClient() as subscriber, caplog.at_level(logging.ERROR):
        depths = monitor.check(subscriber, [subscription])

    assert depths == {subscription: 0}
    assert caplog.records == []
