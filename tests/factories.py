"""Fabriques de données de test."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pandas as pd

T0 = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)


def history_from_prices(
    series: dict[int, Sequence[float]],
    *,
    category: str = "Poetry",
    start: datetime = T0,
) -> pd.DataFrame:
    """Construit un historique (une observation par jour) à partir de listes de prix."""
    rows = []
    for pid, prices in series.items():
        for day, price in enumerate(prices):
            rows.append(
                {
                    "product_id": pid,
                    "source": "books",
                    "url": f"https://a.test/{pid}",
                    "title": f"Produit {pid}",
                    "category": category,
                    "price": float(price),
                    "currency": "GBP",
                    "price_base": float(price) * 1.15,
                    "in_stock": True,
                    "stock_qty": 3,
                    "rating": 4,
                    "collected_at": pd.Timestamp(start + timedelta(days=day)),
                }
            )
    return pd.DataFrame(rows)
