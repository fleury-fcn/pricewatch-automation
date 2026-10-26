import doctest
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from pricewatch import cleaning
from pricewatch.cleaning import clean_products, parse_availability, parse_price, to_dataframe
from pricewatch.collectors.base import RawProduct
from pricewatch.config import CleaningSettings

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)


def raw(**overrides: object) -> RawProduct:
    data: dict[str, object] = {
        "source": "books",
        "url": "https://books.toscrape.com/catalogue/x_1/index.html",
        "title": "  Un   livre ",
        "price_raw": "£51.77",
        "availability_raw": "In stock (22 available)",
        "category": "Poetry",
        "rating": 3,
        "collected_at": NOW,
    }
    return RawProduct(**{**data, **overrides})  # type: ignore[arg-type]


def test_doctests() -> None:
    assert doctest.testmod(cleaning).failed == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£51.77", (51.77, "GBP")),
        ("Â£51.77", (51.77, "GBP")),
        ("51,77 €", (51.77, "EUR")),
        ("1 234,56 €", (1234.56, "EUR")),
        ("1 234,56 €", (1234.56, "EUR")),
        ("$1,234.56", (1234.56, "USD")),
        ("1.234,56 EUR", (1234.56, "EUR")),
        ("CHF 1'299.90", (1299.9, "CHF")),
        ("1,234", (1234.0, None)),
        ("1.234.567", (1234567.0, None)),
        ("12.50", (12.5, None)),
        ("Prix : nc", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
        (float("nan"), (None, None)),
    ],
)
def test_parse_price(text: str | None, expected: tuple[float | None, str | None]) -> None:
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In stock (22 available)", (True, 22)),
        ("In stock", (True, None)),
        ("En stock", (True, None)),
        ("0 available", (False, 0)),
        ("Out of stock", (False, 0)),
        ("Rupture de stock", (False, 0)),
        ("Sur commande", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_availability(text: str | None, expected: tuple[bool | None, int | None]) -> None:
    assert parse_availability(text) == expected


def test_clean_products_happy_path() -> None:
    result = clean_products([raw()])
    row = result.data.iloc[0]
    assert row["title"] == "Un livre"
    assert row["price"] == pytest.approx(51.77)
    assert row["currency"] == "GBP"
    assert row["price_base"] == pytest.approx(round(51.77 * 1.15, 2))
    assert bool(row["in_stock"]) is True and row["stock_qty"] == 22
    assert str(row["collected_at"].tz) == "UTC"
    assert result.report.n_valid == 1 and result.report.n_rejected == 0


def test_duplicates_keep_latest_observation() -> None:
    older = raw(price_raw="£60.00", collected_at=NOW - timedelta(hours=1))
    newer = raw(price_raw="£50.00")
    result = clean_products([newer, older])
    assert result.report.n_duplicates == 1
    assert len(result.data) == 1
    assert result.data.iloc[0]["price"] == 50.0


def test_missing_and_invalid_values_are_rejected() -> None:
    items = [
        raw(url="https://a.test/1", price_raw="gratuit"),
        raw(url="https://a.test/2", title="   "),
        raw(url="https://a.test/3", price_raw="£0.00"),
        raw(url="https://a.test/4", price_raw="£999999"),
        raw(url="https://a.test/5", rating=9),
        raw(url="not-a-url"),
        raw(url="https://a.test/7"),
    ]
    result = clean_products(items)
    reasons = [r.reason for r in result.report.rejections]
    assert result.report.n_valid == 1
    assert result.report.n_rejected == 6
    assert any("prix illisible" in r for r in reasons)
    assert any("title manquant" in r for r in reasons)
    assert sum("hors bornes" in r for r in reasons) == 2
    assert any("rating" in r for r in reasons)
    assert any("url" in r for r in reasons)


def test_default_currency_and_unknown_rate() -> None:
    settings = CleaningSettings(default_currency="JPY", exchange_rates={"EUR": 1.0})
    result = clean_products([raw(price_raw="1,500")], settings)
    row = result.data.iloc[0]
    assert row["currency"] == "JPY"
    assert row["price"] == 1500
    assert pd.isna(row["price_base"])


def test_optional_fields_can_be_missing() -> None:
    result = clean_products([raw(availability_raw=None, category=None, rating=None)])
    row = result.data.iloc[0]
    assert pd.isna(row["in_stock"]) and pd.isna(row["category"]) and pd.isna(row["rating"])


def test_naive_datetime_is_localised_to_utc() -> None:
    result = clean_products([raw(collected_at=datetime(2026, 1, 1, 12, 0))])
    assert result.data.iloc[0]["collected_at"] == pd.Timestamp("2026-01-01 12:00", tz="UTC")


def test_accepts_dataframe_and_empty_input() -> None:
    df = to_dataframe([raw()])
    assert clean_products(df).report.n_valid == 1
    empty = clean_products([])
    assert empty.data.empty and list(empty.data.columns) == cleaning.CLEAN_COLUMNS


def test_invalid_exchange_rate_config() -> None:
    with pytest.raises(ValueError):
        CleaningSettings(exchange_rates={"usd": -1})
