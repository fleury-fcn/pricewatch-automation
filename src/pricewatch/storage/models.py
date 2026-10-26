"""Modèle relationnel : produits, historique de prix, exécutions et alertes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Horodatage courant en UTC (timezone-aware)."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stocke des datetimes en UTC et les relit toujours timezone-aware.

    SQLite ne conserve pas les fuseaux : sans ce type, une date écrite « aware » serait
    relue « naive », ce qui fausse les comparaisons.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convertit en UTC naïf avant écriture (une date naïve est supposée UTC)."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        """Rend la date relue timezone-aware (UTC)."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    """Base déclarative commune."""


class Product(Base):
    """Produit suivi (identifié de façon unique par son URL)."""

    __tablename__ = "products"
    __table_args__ = (Index("ix_products_source_external_id", "source", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(200), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    prices: Mapped[list[PriceRecord]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Run(Base):
    """Exécution du pipeline (traçabilité et supervision)."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    mode: Mapped[str] = mapped_column(String(20), default="live")
    n_collected: Mapped[int] = mapped_column(Integer, default=0)
    n_valid: Mapped[int] = mapped_column(Integer, default=0)
    n_rejected: Mapped[int] = mapped_column(Integer, default=0)
    n_stored: Mapped[int] = mapped_column(Integer, default=0)
    n_alerts: Mapped[int] = mapped_column(Integer, default=0)
    n_requests: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class PriceRecord(Base):
    """Observation de prix d'un produit à un instant donné."""

    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("product_id", "collected_at", name="uq_price_product_time"),
        Index("ix_price_product_time", "product_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    price_base: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    stock_qty: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    product: Mapped[Product] = relationship(back_populates="prices")


class Alert(Base):
    """Alerte émise lors d'une exécution."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30), index=True)
    message: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    delivered_to: Mapped[str | None] = mapped_column(String(200))

    product: Mapped[Product] = relationship()
