"""alerts and processed events

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notifier.alerts (
            alert_id uuid PRIMARY KEY,
            merchant_id text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('segment_changed', 'risk_check_required')),
            check_id uuid UNIQUE,
            status text CHECK (status IN ('open', 'closed')),
            decision text CHECK (decision IN ('approve_lower', 'keep_current', 'superseded')),
            details jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            closed_at timestamptz,
            CHECK (
                (kind = 'risk_check_required' AND check_id IS NOT NULL AND status IS NOT NULL)
                OR (kind = 'segment_changed' AND check_id IS NULL AND status IS NULL)
            ),
            CHECK ((status = 'closed') = (closed_at IS NOT NULL AND decision IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX alerts_merchant_created ON notifier.alerts (merchant_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE notifier.processed_events (
            event_id uuid PRIMARY KEY,
            processed_at timestamptz NOT NULL
        )
        """
    )
    op.execute("GRANT SELECT, INSERT ON notifier.alerts TO notifier")
    op.execute("GRANT UPDATE (status, decision, details, closed_at) ON notifier.alerts TO notifier")
    op.execute("GRANT SELECT, INSERT, DELETE ON notifier.processed_events TO notifier")


def downgrade() -> None:
    op.execute("DROP TABLE notifier.processed_events")
    op.execute("DROP TABLE notifier.alerts")
