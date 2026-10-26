"""Jeu de données de démonstration : historique simulé, import/export CSV.

Le dashboard hébergé (Streamlit Community Cloud) dispose d'un stockage éphémère : il
charge donc un jeu de données versionné dans ``data/demo/`` plutôt qu'une base locale.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from pricewatch.cleaning import CLEAN_COLUMNS, clean_products
from pricewatch.collectors import RawProduct, collect_all
from pricewatch.config import AppConfig
from pricewatch.formatting import CURRENCY_SYMBOLS
from pricewatch.logging_setup import get_logger
from pricewatch.simulation import generate_history
from pricewatch.storage import PriceRepository

logger = get_logger(__name__)

DEMO_DIR = Path("data/demo")
CATALOGUE_FILE = "catalogue.csv"
HISTORY_FILE = "price_history.csv.gz"


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True, format="ISO8601")
    for column in ("in_stock",):
        if column in df:
            df[column] = df[column].astype("boolean")
    for column in ("stock_qty", "rating"):
        if column in df:
            df[column] = df[column].astype("Int64")
    return df


def load_catalogue(demo_dir: Path | None = None) -> pd.DataFrame:
    """Catalogue de référence (produits réels collectés, prix de base)."""
    return _read_csv((demo_dir or DEMO_DIR) / CATALOGUE_FILE)


def build_catalogue(config: AppConfig, demo_dir: Path | None = None) -> pd.DataFrame:
    """Collecte le catalogue réel (sans simulation) et l'enregistre en CSV."""
    demo_dir = demo_dir or DEMO_DIR
    collection = collect_all(config)
    catalogue = clean_products(collection.products, config.cleaning).data
    if catalogue.empty:
        raise RuntimeError(f"Collecte vide, catalogue non créé : {collection.errors[:3]}")
    demo_dir.mkdir(parents=True, exist_ok=True)
    catalogue.to_csv(demo_dir / CATALOGUE_FILE, index=False)
    logger.info("Catalogue de démo : %d produits", len(catalogue))
    return catalogue


def catalogue_as_raw(
    catalogue: pd.DataFrame, collected_at: datetime | None = None
) -> list[RawProduct]:
    """Convertit le catalogue en enregistrements bruts (collecte hors ligne simulée).

    Permet d'exécuter tout le pipeline sans réseau, par exemple depuis le dashboard
    si books.toscrape.com est injoignable.
    """
    when = collected_at or datetime.now(UTC)

    def _opt(value: object) -> str | None:
        return None if pd.isna(value) else str(value)  # type: ignore[arg-type]

    return [
        RawProduct(
            source=str(row.source),
            url=str(row.url),
            title=str(row.title),
            price_raw=f"{CURRENCY_SYMBOLS.get(str(row.currency), str(row.currency))}{row.price:.2f}",
            availability_raw=(
                None
                if pd.isna(row.in_stock)
                else (
                    f"In stock ({int(row.stock_qty)} available)"
                    if row.in_stock and pd.notna(row.stock_qty)
                    else "In stock" if row.in_stock else "Out of stock"
                )
            ),
            category=_opt(row.category),
            external_id=_opt(row.external_id),
            rating=None if pd.isna(row.rating) else int(row.rating),
            image_url=_opt(row.image_url),
            collected_at=when,
        )
        for row in catalogue.itertuples(index=False)
    ]


def seed_history(
    repo: PriceRepository,
    config: AppConfig,
    days: int = 90,
    catalogue: pd.DataFrame | None = None,
    end: date | None = None,
) -> int:
    """Remplit la base avec un historique simulé de ``days`` jours.

    L'historique s'arrête **la veille** par défaut : l'exécution du jour ajoute ensuite
    son propre point, cohérent avec la série (simulation déterministe).

    Returns:
        Nombre d'observations insérées.
    """
    catalogue = load_catalogue() if catalogue is None else catalogue
    end = end or (datetime.now(UTC).date() - timedelta(days=1))
    history = generate_history(
        catalogue[CLEAN_COLUMNS], days, end=end, rates=config.cleaning.exchange_rates
    )
    repo.init_db()
    inserted = repo.save_observations(history)
    logger.info("Historique de démo : %d observations sur %d jours", inserted, days)
    return inserted


def export_history(repo: PriceRepository, demo_dir: Path | None = None) -> Path:
    """Exporte tout l'historique de la base en CSV (jeu de données du dashboard)."""
    demo_dir = demo_dir or DEMO_DIR
    demo_dir.mkdir(parents=True, exist_ok=True)
    history = repo.get_price_history()
    products = repo.get_products()[["id", "external_id", "image_url"]]
    merged = history.merge(products, left_on="product_id", right_on="id", how="left")
    path = demo_dir / HISTORY_FILE
    merged[CLEAN_COLUMNS].to_csv(path, index=False)
    logger.info("Historique exporté : %s (%d lignes)", path, len(merged))
    return path


def load_history(repo: PriceRepository, path: Path | None = None) -> int:
    """Charge un historique CSV dans la base (idempotent)."""
    repo.init_db()
    return repo.save_observations(_read_csv(path or DEMO_DIR / HISTORY_FILE))
