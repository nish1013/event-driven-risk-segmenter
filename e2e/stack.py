import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from google.cloud import pubsub_v1

RECORDING_URL = os.environ.get("RECORDING_URL", "http://localhost:8001")
SEGMENTER_URL = os.environ.get("SEGMENTER_URL", "http://localhost:8002")
NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://localhost:8003")
REPO_ROOT = Path(__file__).parent.parent
PROJECT_ID = os.environ["PUBSUB_PROJECT_ID"]
WAIT_S = 30.0

type Event = dict[str, Any]


class TopicTap:
    """An extra subscription on a topic: what any new consumer would receive."""

    def __init__(self, topic: str) -> None:
        self._subscriber = pubsub_v1.SubscriberClient()
        self.subscription = self._subscriber.subscription_path(PROJECT_ID, f"e2e-tap-{uuid4().hex}")
        self._subscriber.create_subscription(
            request={"name": self.subscription, "topic": f"projects/{PROJECT_ID}/topics/{topic}"}
        )

    def messages(
        self, matching: Callable[[Event], bool], wait: float
    ) -> list[pubsub_v1.types.PubsubMessage]:
        found: list[pubsub_v1.types.PubsubMessage] = []
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            response = self._subscriber.pull(
                subscription=self.subscription, max_messages=10, timeout=2
            )
            if response.received_messages:
                self._subscriber.acknowledge(
                    subscription=self.subscription,
                    ack_ids=[received.ack_id for received in response.received_messages],
                )
            found += [
                received.message
                for received in response.received_messages
                if matching(json.loads(received.message.data))
            ]
        return found

    def delete(self) -> None:
        self._subscriber.delete_subscription(subscription=self.subscription)
        self._subscriber.close()


def activity(merchant: str, activity_type: str, occurred_at: str) -> dict[str, Any]:
    payloads: dict[str, dict[str, str]] = {
        "chargeback": {
            "transactionId": f"t-{uuid4()}",
            "amount": "120.00",
            "currency": "GBP",
            "reasonCode": "4837",
        },
        "failed_login": {"userId": "u-1", "method": "password"},
    }
    return {
        "activityId": f"a-{uuid4()}",
        "merchantId": merchant,
        "type": activity_type,
        "occurredAt": occurred_at,
        "payload": payloads[activity_type],
    }


def wait_for[T](read: Callable[[], T], done: Callable[[T], bool], what: str) -> T:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(0.5)
    pytest.fail(f"{what} not reached within {WAIT_S}s")


def post_activity(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{RECORDING_URL}/activities",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _send(request)


def get_segment(merchant_id: str) -> tuple[int, dict[str, Any]]:
    return _send(urllib.request.Request(f"{SEGMENTER_URL}/merchants/{merchant_id}/segment"))


def get_alerts(merchant_id: str, status: str | None = None) -> list[dict[str, Any]]:
    query = "" if status is None else f"?status={status}"
    with urllib.request.urlopen(
        f"{NOTIFIER_URL}/merchants/{merchant_id}/alerts{query}", timeout=5
    ) as response:
        alerts: list[dict[str, Any]] = json.loads(response.read())
    return alerts


def post_resolution(check_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{SEGMENTER_URL}/risk-checks/{check_id}/resolution",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _send(request)


def compose(*args: str) -> str:
    done = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    )
    return done.stdout + done.stderr


def psql(sql: str) -> None:
    psql_args = ["-U", "postgres", "-d", "risk_segmenter", "-v", "ON_ERROR_STOP=1", "-c", sql]
    compose("exec", "-T", "postgres", "psql", *psql_args)


def _send(request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
