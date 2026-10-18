import os

import pytest
from google.cloud import pubsub_v1


@pytest.mark.parametrize(
    "subscription", ["segmenter-activity-recorded", "notifier-segmenter-events"]
)
def test_subscription_is_ordered_with_dead_letter(subscription: str) -> None:
    project_id = os.environ["PUBSUB_PROJECT_ID"]
    with pubsub_v1.SubscriberClient() as subscriber:
        found = subscriber.get_subscription(
            subscription=subscriber.subscription_path(project_id, subscription)
        )
        dead_letter = subscriber.get_subscription(
            subscription=subscriber.subscription_path(project_id, f"{subscription}-dead-letter")
        )

    assert found.enable_message_ordering
    assert found.dead_letter_policy.max_delivery_attempts == 5
    assert found.dead_letter_policy.dead_letter_topic == dead_letter.topic
