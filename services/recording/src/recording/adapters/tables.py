from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="recording")

activities = Table(
    "activities",
    metadata,
    Column("activity_id", Text, primary_key=True),
    Column("merchant_id", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", Uuid, nullable=False),
    Column("activity_id", Text, nullable=False),
    Column("topic", Text, nullable=False),
    Column("ordering_key", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True)),
)
