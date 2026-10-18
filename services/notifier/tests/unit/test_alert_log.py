import json
import logging

import pytest
from samples import changed

from notifier.adapters.alert_log import StructuredAlertLog
from notifier.domain.alert import Alert
from notifier.domain.events import RiskSegmentChanged


def test_us6_12_one_structured_log_line(caplog: pytest.LogCaptureFixture) -> None:
    event: RiskSegmentChanged = changed("low", "medium")
    alert = Alert.of(event, event.event_id)
    logger = logging.getLogger("notifier.test.alerts")

    with caplog.at_level(logging.INFO, logger=logger.name):
        StructuredAlertLog(logger).write(alert)

    assert len(caplog.records) == 1
    line = json.loads(caplog.records[0].getMessage())
    assert (line["kind"], line["merchantId"], line["details"]["to"]) == (
        "segment_changed",
        "m-200",
        "medium",
    )
