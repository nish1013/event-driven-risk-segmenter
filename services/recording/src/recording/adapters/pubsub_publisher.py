import json
import logging
import os
from collections.abc import Sequence
from concurrent.futures import Future

from google.cloud import pubsub_v1

from recording.adapters.outbox import OutboxMessage

logger = logging.getLogger(__name__)


class PubSubPublisher:
    def __init__(
        self, client: pubsub_v1.PublisherClient, project_id: str, ack_timeout: float
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._ack_timeout = ack_timeout

    @classmethod
    def from_env(cls) -> PubSubPublisher:
        client = pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
        )
        return cls(client, os.environ["PUBSUB_PROJECT_ID"], ack_timeout=30.0)

    def publish(self, messages: Sequence[OutboxMessage]) -> list[bool]:
        futures = [self._submit(message) for message in messages]
        return [
            self._acked(message, future) for message, future in zip(messages, futures, strict=True)
        ]

    def _submit(self, message: OutboxMessage) -> Future[str] | None:
        try:
            future: Future[str] = self._client.publish(
                self._client.topic_path(self._project_id, message.topic),
                json.dumps(message.body, separators=(",", ":")).encode(),
                ordering_key=message.ordering_key,
                eventId=str(message.event_id),
                eventType=str(message.body["eventType"]),
            )
        except Exception:
            logger.exception("publish of outbox row %s refused", message.id)
            return None
        return future

    def _acked(self, message: OutboxMessage, future: Future[str] | None) -> bool:
        if future is not None and self._wait(message, future):
            return True
        # The client pauses an ordering key after a failure until it is resumed.
        self._client.resume_publish(
            self._client.topic_path(self._project_id, message.topic), message.ordering_key
        )
        return False

    def _wait(self, message: OutboxMessage, future: Future[str]) -> bool:
        try:
            future.result(timeout=self._ack_timeout)
        except Exception:
            logger.warning("outbox row %s not acked", message.id, exc_info=True)
            return False
        return True
