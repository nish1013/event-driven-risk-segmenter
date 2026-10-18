import json
import logging

from notifier.domain.alert import Alert


class StructuredAlertLog:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def write(self, alert: Alert) -> None:
        self._logger.info(
            json.dumps(
                {
                    "alertId": str(alert.alert_id),
                    "merchantId": alert.merchant_id,
                    "kind": alert.kind.value,
                    "checkId": None if alert.check_id is None else str(alert.check_id),
                    "status": None if alert.status is None else alert.status.value,
                    "decision": None if alert.decision is None else alert.decision.value,
                    "details": alert.details,
                    "createdAt": alert.created_at.isoformat(),
                },
                separators=(",", ":"),
            )
        )
