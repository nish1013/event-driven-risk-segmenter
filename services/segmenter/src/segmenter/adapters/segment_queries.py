from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, and_, select

from segmenter.adapters.tables import checks, segments


@dataclass(frozen=True)
class OpenCheckView:
    check_id: UUID
    proposed_band: str
    opened_at: datetime


@dataclass(frozen=True)
class SegmentView:
    merchant_id: str
    band: str
    rule: str | None
    policy_version: int
    updated_at: datetime
    open_check: OpenCheckView | None


class SqlSegmentQueries:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def segment(self, merchant_id: str) -> SegmentView | None:
        query = (
            select(
                segments.c.merchant_id,
                segments.c.band,
                segments.c.rule,
                segments.c.policy_version,
                segments.c.updated_at,
                checks.c.check_id,
                checks.c.proposed_band,
                checks.c.opened_at,
            )
            .select_from(
                segments.outerjoin(
                    checks,
                    and_(
                        checks.c.merchant_id == segments.c.merchant_id,
                        checks.c.status == "open",
                    ),
                )
            )
            .where(segments.c.merchant_id == merchant_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(query).mappings().one_or_none()
        if row is None:
            return None
        open_check = None
        if row["check_id"] is not None:
            open_check = OpenCheckView(row["check_id"], row["proposed_band"], row["opened_at"])
        return SegmentView(
            merchant_id=row["merchant_id"],
            band=row["band"],
            rule=row["rule"],
            policy_version=row["policy_version"],
            updated_at=row["updated_at"],
            open_check=open_check,
        )
