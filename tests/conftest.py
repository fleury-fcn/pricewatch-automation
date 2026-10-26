"""Fixtures partagées."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project_config_path() -> Path:
    """Chemin du fichier de configuration du projet."""
    return ROOT / "config" / "config.yaml"


@pytest.fixture
def test_config(project_config_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Configuration du projet isolée dans ``tmp_path`` (base, rapports, pas de délai)."""
    from pricewatch.config import load_config

    config = load_config(project_config_path)
    config.storage.database_url = f"sqlite:///{tmp_path}/pw.db"
    config.report.output_dir = tmp_path / "reports"
    config.logging.file = None
    config.collection.delay_seconds = 0
    config.collection.max_retries = 0
    config.sources[0].max_pages = 1
    return config


@pytest.fixture
def listing_products():  # type: ignore[no-untyped-def]
    """20 RawProduct issus de la vraie page 1 du catalogue."""
    from pricewatch.collectors.books_toscrape import parse_listing

    html = (FIXTURES / "books_list_page1.html").read_bytes()
    products, _ = parse_listing(
        html, "https://books.toscrape.com/catalogue/page-1.html", "books_toscrape"
    )
    return products
