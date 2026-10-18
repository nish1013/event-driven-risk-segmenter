"""segments, facts, checks, outbox and processed events

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
        CREATE TABLE segmenter.segments (
            merchant_id text PRIMARY KEY,
            band text NOT NULL CHECK (band IN ('low', 'medium', 'high')),
            rule text,
            policy_version integer NOT NULL,
            version_number bigint NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE segmenter.facts (
            activity_id text PRIMARY KEY,
            merchant_id text NOT NULL,
            type text NOT NULL,
            occurred_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX facts_merchant_type_occurred ON segmenter.facts "
        "(merchant_id, type, occurred_at)"
    )
    op.execute(
        """
        CREATE TABLE segmenter.checks (
            check_id uuid PRIMARY KEY,
            merchant_id text NOT NULL REFERENCES segmenter.segments (merchant_id),
            proposed_band text NOT NULL CHECK (proposed_band IN ('low', 'medium', 'high')),
            status text NOT NULL CHECK (status IN ('open', 'resolved')),
            decision text CHECK (decision IN ('approve_lower', 'keep_current', 'superseded')),
            officer_id text,
            reason text,
            opened_at timestamptz NOT NULL,
            resolved_at timestamptz
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX checks_one_open_per_merchant ON segmenter.checks (merchant_id) "
        "WHERE status = 'open'"
    )
    op.execute(
        """
        CREATE TABLE segmenter.outbox (
            id bigserial PRIMARY KEY,
            event_id uuid NOT NULL UNIQUE,
            topic text NOT NULL,
            ordering_key text NOT NULL,
            body jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            sent_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX outbox_unsent ON segmenter.outbox (id) WHERE sent_at IS NULL")
    op.execute(
        """
        CREATE TABLE segmenter.processed_events (
            event_id uuid PRIMARY KEY,
            processed_at timestamptz NOT NULL
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON segmenter.segments TO segmenter")
    op.execute("GRANT SELECT, INSERT, DELETE ON segmenter.facts TO segmenter")
    op.execute("GRANT SELECT, INSERT, UPDATE ON segmenter.checks TO segmenter")
    op.execute("GRANT SELECT, INSERT, DELETE ON segmenter.outbox TO segmenter")
    op.execute("GRANT UPDATE (sent_at) ON segmenter.outbox TO segmenter")
    op.execute("GRANT USAGE ON SEQUENCE segmenter.outbox_id_seq TO segmenter")
    op.execute("GRANT SELECT, INSERT, DELETE ON segmenter.processed_events TO segmenter")


def downgrade() -> None:
    op.execute("DROP TABLE segmenter.processed_events")
    op.execute("DROP TABLE segmenter.outbox")
    op.execute("DROP TABLE segmenter.checks")
    op.execute("DROP TABLE segmenter.facts")
    op.execute("DROP TABLE segmenter.segments")
