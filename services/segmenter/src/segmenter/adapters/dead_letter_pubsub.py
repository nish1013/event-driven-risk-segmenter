from google.api_core.exceptions import DeadlineExceeded
from google.cloud import pubsub_v1

from segmenter.service_layer.replay import ParkedMessage


class PubSubDeadLetters:
    def __init__(
        self,
        client: pubsub_v1.SubscriberClient,
        subscription: str,
        batch_size: int,
        pull_timeout: float,
    ) -> None:
        self._client = client
        self._subscription = subscription
        self._batch_size = batch_size
        self._pull_timeout = pull_timeout

    def pull(self) -> list[ParkedMessage]:
        try:
            response = self._client.pull(
                subscription=self._subscription,
                max_messages=self._batch_size,
                timeout=self._pull_timeout,
            )
        except DeadlineExceeded:
            return []
        return [
            ParkedMessage(
                ack_id=received.ack_id,
                data=received.message.data,
                attributes=dict(received.message.attributes),
                ordering_key=received.message.ordering_key,
            )
            for received in response.received_messages
        ]

    def ack(self, message: ParkedMessage) -> None:
        self._client.acknowledge(subscription=self._subscription, ack_ids=[message.ack_id])

    def release(self, messages: list[ParkedMessage]) -> None:
        if messages:
            self._client.modify_ack_deadline(
                subscription=self._subscription,
                ack_ids=[message.ack_id for message in messages],
                ack_deadline_seconds=0,
            )


class PubSubSourceTopic:
    def __init__(self, client: pubsub_v1.PublisherClient, topic: str, ack_timeout: float) -> None:
        self._client = client
        self._topic = topic
        self._ack_timeout = ack_timeout

    def republish(self, message: ParkedMessage) -> None:
        future = self._client.publish(
            self._topic, message.data, ordering_key=message.ordering_key, **message.attributes
        )
        try:
            future.result(timeout=self._ack_timeout)
        except Exception:
            if message.ordering_key:
                # The client pauses an ordering key after a failure until it is resumed.
                self._client.resume_publish(self._topic, message.ordering_key)
            raise
