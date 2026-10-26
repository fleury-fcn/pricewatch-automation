from datetime import UTC, date, datetime

import pandas as pd
import pytest

from pricewatch.simulation import apply_simulation, generate_history, simulated_price


def test_simulated_price_is_deterministic_and_bounded() -> None:
    day = date(2026, 10, 26)
    a = simulated_price(50.0, "https://a.test/1", day)
    assert a == simulated_price(50.0, "https://a.test/1", day)
    prices = [
        simulated_price(50.0, "https://a.test/1", date.fromordinal(day.toordinal() + i))
        for i in range(365)
    ]
    assert all(0 < p < 50 * 2.2 for p in prices)
    assert len(set(prices)) > 250  # le prix varie réellement (au centime près)


def test_simulation_contains_promotions_and_spikes() -> None:
    days = [date.fromordinal(date(2026, 1, 1).toordinal() + i) for i in range(365)]
    ratios = [
        simulated_price(100.0, f"https://a.test/{k}", d) / 100 for k in range(30) for d in days
    ]
    assert min(ratios) < 0.8, "des promotions profondes sont attendues"
    assert max(ratios) > 1.25, "quelques pics anormaux sont attendus"


def test_apply_simulation_only_on_selected_sources() -> None:
    ts = pd.Timestamp(datetime(2026, 10, 26, 6, tzinfo=UTC))
    df = pd.DataFrame(
        {
            "source": ["sim", "real"],
            "url": ["https://a.test/1", "https://a.test/2"],
            "price": [50.0, 50.0],
            "currency": ["GBP", "XXX"],
            "price_base": [57.5, None],
            "collected_at": [ts, ts],
        }
    )
    out = apply_simulation(df, {"sim"}, {"GBP": 1.15})
    assert out.loc[1, "price"] == 50.0
    assert out.loc[0, "price"] == simulated_price(50.0, "https://a.test/1", ts.date())
    assert out.loc[0, "price_base"] == pytest.approx(round(out.loc[0, "price"] * 1.15, 2))
    assert df.loc[0, "price"] == 50.0  # l'original n'est pas modifié
    assert apply_simulation(df, set()).equals(df)


def test_generate_history() -> None:
    catalogue = pd.DataFrame(
        {
            "source": ["books", "books"],
            "url": ["https://a.test/1", "https://a.test/2"],
            "title": ["A", "B"],
            "price": [10.0, 20.0],
            "currency": ["GBP", "GBP"],
            "price_base": [11.5, 23.0],
            "collected_at": [pd.Timestamp.now(tz="UTC")] * 2,
        }
    )
    history = generate_history(catalogue, days=30, end=date(2026, 10, 26))
    assert len(history) == 60
    assert history["collected_at"].max() == pd.Timestamp("2026-10-26 06:00", tz="UTC")
    assert history["collected_at"].min() == pd.Timestamp("2026-09-27 06:00", tz="UTC")
    assert history.groupby("url")["price"].nunique().min() > 20
    with pytest.raises(ValueError):
        generate_history(catalogue, days=0)
