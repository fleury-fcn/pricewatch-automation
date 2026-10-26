"""Le jeu de données embarqué doit suffire à faire tourner la démo (Streamlit Cloud)."""

from pathlib import Path

import pandas as pd
import pytest

from pricewatch import demo
from pricewatch.cleaning import CLEAN_COLUMNS, clean_products

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "data" / "demo"


def test_bundled_catalogue_is_valid() -> None:
    catalogue = demo.load_catalogue(DEMO_DIR)
    assert len(catalogue) == 60
    assert catalogue["url"].is_unique
    assert (catalogue["price"] > 0).all()
    assert set(CLEAN_COLUMNS) <= set(catalogue.columns)
    assert "Default" not in set(catalogue["category"].dropna())


def test_bundled_history_is_valid() -> None:
    history = pd.read_csv(DEMO_DIR / demo.HISTORY_FILE)
    assert list(history.columns) == CLEAN_COLUMNS
    assert len(history) >= 60 * 90
    assert history.groupby("url").size().min() >= 90
    assert not history.duplicated(["url", "collected_at"]).any()


def test_catalogue_as_raw_roundtrip() -> None:
    catalogue = demo.load_catalogue(DEMO_DIR)
    raw = demo.catalogue_as_raw(catalogue)
    cleaned = clean_products(raw).data.set_index("url")
    original = catalogue.set_index("url")
    assert cleaned.index.equals(original.index)
    assert (cleaned["price"] == original["price"]).all()
    assert (cleaned["stock_qty"].fillna(-1) == original["stock_qty"].fillna(-1)).all()


def test_catalogue_as_raw_availability_variants() -> None:
    catalogue = demo.load_catalogue(DEMO_DIR).head(3).copy()
    catalogue["in_stock"] = pd.array([True, False, None], dtype="boolean")
    catalogue["stock_qty"] = pd.array([None, 0, None], dtype="Int64")
    raw = demo.catalogue_as_raw(catalogue)
    assert [r.availability_raw for r in raw] == ["In stock", "Out of stock", None]


def test_dashboard_defaults_to_demo_without_local_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("PRICEWATCH_MODE", raising=False)
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    expected = "Mode démo" if not (ROOT / "data" / "pricewatch.db").is_file() else "Mode local"
    sidebar_text = " ".join(e.value for e in [*at.sidebar.info, *at.sidebar.success])
    assert expected in sidebar_text
