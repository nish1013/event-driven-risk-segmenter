"""activities and outbox

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
        CREATE TABLE recording.activities (
            activity_id text PRIMARY KEY,
            merchant_id text NOT NULL,
            type text NOT NULL
                CHECK (type IN ('chargeback', 'velocity_breach', 'kyc_expired', 'failed_login')),
            occurred_at timestamptz NOT NULL,
            payload jsonb NOT NULL,
            recorded_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE recording.outbox (
            id bigserial PRIMARY KEY,
            event_id uuid NOT NULL UNIQUE,
            activity_id text NOT NULL UNIQUE REFERENCES recording.activities (activity_id),
            topic text NOT NULL,
            ordering_key text NOT NULL,
            body jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            sent_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX outbox_unsent ON recording.outbox (id) WHERE sent_at IS NULL")
    # I2: the runtime role inserts and reads activities, never updates or deletes them (#36).
    op.execute("GRANT SELECT, INSERT ON recording.activities TO recording")
    op.execute("GRANT SELECT, INSERT, DELETE ON recording.outbox TO recording")
    op.execute("GRANT UPDATE (sent_at) ON recording.outbox TO recording")
    op.execute("GRANT USAGE ON SEQUENCE recording.outbox_id_seq TO recording")


def downgrade() -> None:
    op.execute("DROP TABLE recording.outbox")
    op.execute("DROP TABLE recording.activities")
