import os
import random
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.subscriber.message import Message
from google.protobuf import duration_pb2

PROJECT_ID = os.environ["PUBSUB_PROJECT_ID"]
MAX_DELIVERY_ATTEMPTS = 5
TIMEOUT_S = 60.0


@dataclass
class Topology:
    topic: str
    subscription: str
    dead_letter_topic: str
    dead_letter_subscription: str


@dataclass
class Received:
    by_key: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    lock: threading.Lock = field(default_factory=threading.Lock)


@pytest.fixture
def publisher() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient(
        publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
    )


@pytest.fixture
def subscriber() -> Iterator[pubsub_v1.SubscriberClient]:
    client = pubsub_v1.SubscriberClient()
    yield client
    client.close()


@pytest.fixture
def topology(
    publisher: pubsub_v1.PublisherClient, subscriber: pubsub_v1.SubscriberClient
) -> Iterator[Topology]:
    suffix = uuid.uuid4().hex[:8]
    names = Topology(
        topic=publisher.topic_path(PROJECT_ID, f"check-{suffix}"),
        subscription=subscriber.subscription_path(PROJECT_ID, f"check-{suffix}"),
        dead_letter_topic=publisher.topic_path(PROJECT_ID, f"check-{suffix}-dead-letter"),
        dead_letter_subscription=subscriber.subscription_path(
            PROJECT_ID, f"check-{suffix}-dead-letter"
        ),
    )
    publisher.create_topic(name=names.topic)
    publisher.create_topic(name=names.dead_letter_topic)
    subscriber.create_subscription(
        name=names.dead_letter_subscription, topic=names.dead_letter_topic
    )
    subscriber.create_subscription(
        request={
            "name": names.subscription,
            "topic": names.topic,
            "enable_message_ordering": True,
            "dead_letter_policy": {
                "dead_letter_topic": names.dead_letter_topic,
                "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
            },
            "retry_policy": {
                "minimum_backoff": duration_pb2.Duration(seconds=0),
                "maximum_backoff": duration_pb2.Duration(seconds=1),
            },
        }
    )
    yield names
    for sub in (names.subscription, names.dead_letter_subscription):
        subscriber.delete_subscription(subscription=sub)
    for topic in (names.topic, names.dead_letter_topic):
        publisher.delete_topic(topic=topic)


def test_order_per_key_with_concurrent_handlers(
    publisher: pubsub_v1.PublisherClient,
    subscriber: pubsub_v1.SubscriberClient,
    topology: Topology,
) -> None:
    keys = ["m-1", "m-2"]
    per_key = 50
    futures = [
        publisher.publish(topology.topic, b"", ordering_key=key, seq=str(seq))
        for seq in range(per_key)
        for key in keys
    ]
    for future in futures:
        future.result(timeout=TIMEOUT_S)

    received = Received()
    done = threading.Event()

    def handle(message: Message) -> None:
        time.sleep(random.uniform(0, 0.02))
        with received.lock:
            received.by_key[message.ordering_key].append(int(message.attributes["seq"]))
            if sum(len(seqs) for seqs in received.by_key.values()) >= per_key * len(keys):
                done.set()
        message.ack()

    future = subscriber.subscribe(topology.subscription, handle)
    try:
        assert done.wait(TIMEOUT_S), f"received {dict(received.by_key)}"
    finally:
        future.cancel()
        future.result()

    assert received.by_key == {key: list(range(per_key)) for key in keys}


def test_nacked_message_dead_letters_after_5_attempts(
    publisher: pubsub_v1.PublisherClient,
    subscriber: pubsub_v1.SubscriberClient,
    topology: Topology,
) -> None:
    publisher.publish(topology.topic, b"poison", ordering_key="m-1").result(TIMEOUT_S)
    publisher.publish(topology.topic, b"next", ordering_key="m-1").result(TIMEOUT_S)

    attempts: list[int | None] = []
    next_delivered = threading.Event()

    def handle(message: Message) -> None:
        if message.data == b"poison":
            attempts.append(message.delivery_attempt)
            message.nack()
            return
        next_delivered.set()
        message.ack()

    future = subscriber.subscribe(topology.subscription, handle)
    try:
        dead = _pull_one(subscriber, topology.dead_letter_subscription)
        assert next_delivered.wait(TIMEOUT_S), "message after the poison never arrived"
    finally:
        future.cancel()
        future.result()

    assert dead.message.data == b"poison"
    assert attempts == [1, 2, 3, 4, 5]


def _pull_one(
    subscriber: pubsub_v1.SubscriberClient, subscription: str
) -> pubsub_v1.types.ReceivedMessage:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        response = subscriber.pull(subscription=subscription, max_messages=1, timeout=5.0)
        if response.received_messages:
            message = response.received_messages[0]
            subscriber.acknowledge(subscription=subscription, ack_ids=[message.ack_id])
            return message
    raise AssertionError(f"nothing reached {subscription} in {TIMEOUT_S}s")
