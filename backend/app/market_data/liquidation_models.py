from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LiquidationEventRecord(Base):
    __tablename__ = "liquidation_events"
    __table_args__ = (
        Index("ix_liquidation_events_provider_symbol_time", "provider", "symbol", "event_time"),
        Index("ix_liquidation_events_symbol_time_side", "symbol", "event_time", "liquidation_side"),
        Index("ix_liquidation_events_event_time", "event_time"),
    )

    event_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    liquidation_side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price_kind: Mapped[str] = mapped_column(String(24), nullable=False)
