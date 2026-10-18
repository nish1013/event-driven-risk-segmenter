from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="notifier")

alerts = Table(
    "alerts",
    metadata,
    Column("alert_id", Uuid, primary_key=True),
    Column("merchant_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("check_id", Uuid, unique=True),
    Column("status", Text),
    Column("decision", Text),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True)),
)

processed_events = Table(
    "processed_events",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
)
