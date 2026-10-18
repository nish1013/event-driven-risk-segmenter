from datetime import datetime, timedelta

from samples import NOW

from notifier.service_layer.cleanup import clean_processed_events


class FakeProcessedEvents:
    def __init__(self) -> None:
        self.cutoff: datetime | None = None

    def delete_before(self, cutoff: datetime) -> int:
        self.cutoff = cutoff
        return 0


def test_40_processed_events_kept_14_days() -> None:
    processed = FakeProcessedEvents()

    clean_processed_events(processed, NOW)

    assert processed.cutoff == NOW - timedelta(days=14)
