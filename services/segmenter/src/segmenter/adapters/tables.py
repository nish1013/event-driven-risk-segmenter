from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="segmenter")

segments = Table(
    "segments",
    metadata,
    Column("merchant_id", Text, primary_key=True),
    Column("band", Text, nullable=False),
    Column("rule", Text),
    Column("policy_version", Integer, nullable=False),
    Column("version_number", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

facts = Table(
    "facts",
    metadata,
    Column("activity_id", Text, primary_key=True),
    Column("merchant_id", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

checks = Table(
    "checks",
    metadata,
    Column("check_id", Uuid, primary_key=True),
    Column("merchant_id", Text, nullable=False),
    Column("proposed_band", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("decision", Text),
    Column("officer_id", Text),
    Column("reason", Text),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", Uuid, nullable=False),
    Column("topic", Text, nullable=False),
    Column("ordering_key", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True)),
)

processed_events = Table(
    "processed_events",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
)
