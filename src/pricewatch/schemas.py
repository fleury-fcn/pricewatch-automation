"""Schémas de données validés (pydantic) partagés par le pipeline et l'API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CleanProduct(BaseModel):
    """Observation de prix nettoyée et validée, prête à être stockée."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    source: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://\S+$")
    title: str = Field(min_length=1, max_length=500)
    price: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_base: float | None = Field(default=None, gt=0)
    in_stock: bool | None = None
    stock_qty: int | None = Field(default=None, ge=0)
    rating: int | None = Field(default=None, ge=1, le=5)
    category: str | None = None
    external_id: str | None = None
    image_url: str | None = None
    collected_at: datetime

    @field_validator("collected_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("collected_at doit comporter un fuseau horaire")
        return value


class Rejection(BaseModel):
    """Enregistrement écarté pendant le nettoyage, avec la raison."""

    url: str | None
    reason: str


class CleaningReport(BaseModel):
    """Bilan chiffré d'une étape de nettoyage."""

    n_input: int = 0
    n_duplicates: int = 0
    n_rejected: int = 0
    n_valid: int = 0
    rejections: list[Rejection] = Field(default_factory=list)
