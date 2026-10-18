from datetime import datetime, timedelta
from typing import Protocol

# 14 days outlasts the 7-day dead-letter retention, so a replayed event is still recognised (#40).
PROCESSED_RETENTION = timedelta(days=14)


class ProcessedEvents(Protocol):
    def delete_before(self, cutoff: datetime) -> int: ...


def clean_processed_events(processed_events: ProcessedEvents, now: datetime) -> int:
    return processed_events.delete_before(now - PROCESSED_RETENTION)
