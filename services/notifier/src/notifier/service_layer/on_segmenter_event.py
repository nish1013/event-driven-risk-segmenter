import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from notifier.domain.alert import Alert
from notifier.domain.events import SegmenterEvent
from notifier.service_layer.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class AlertLog(Protocol):
    def write(self, alert: Alert) -> None: ...


def on_segmenter_event(
    event: SegmenterEvent,
    unit_of_work: Callable[[], UnitOfWork],
    alert_log: AlertLog,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
) -> Alert | None:
    with unit_of_work() as uow:
        if not uow.mark_processed(event.event_id, now()):
            uow.commit()
            return None
        stored = uow.alerts.record(Alert.of(event, new_id()))
        uow.commit()
    if stored is not None:
        _write_log(alert_log, stored)
    return stored


def _write_log(alert_log: AlertLog, alert: Alert) -> None:
    # A3: the log line is best effort after commit. The alert row is the record.
    try:
        alert_log.write(alert)
    except Exception:
        logger.exception("alert %s stored, log line not written", alert.alert_id)
