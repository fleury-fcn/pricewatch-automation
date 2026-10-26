import numpy as np
import pandas as pd
import pytest

from pricewatch.analysis import (
    category_index,
    compute_product_stats,
    flag_anomalies,
    summarize,
    top_drops,
    top_rises,
    trend_slope_pct,
    zscore,
)
from pricewatch.config import AnalysisSettings
from tests.factories import history_from_prices

SETTINGS = AnalysisSettings(
    significant_drop_pct=10, zscore_threshold=2.5, trend_window_days=14, min_history_points=5
)


def stats_for(series: dict[int, list[float]], **kwargs: object) -> pd.DataFrame:
    return compute_product_stats(history_from_prices(series), SETTINGS, **kwargs).set_index(
        "product_id"
    )


def test_zscore_helpers() -> None:
    assert zscore(10, np.array([10.0, 10.0, 10.0])) == 0.0
    assert zscore(10, np.array([1.0])) == 0.0
    assert zscore(13, np.array([9.0, 10.0, 11.0])) == pytest.approx(3.0)


def test_trend_slope() -> None:
    times = pd.Series(pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC"))
    # pente 1/jour pour un prix moyen de 12 -> 8,33 %/jour
    assert trend_slope_pct(times, np.array([10, 11, 12, 13, 14.0])) == pytest.approx(100 / 12)
    assert trend_slope_pct(times[:1], np.array([10.0])) == 0.0
    same = pd.Series([times.iloc[0]] * 3)
    assert trend_slope_pct(same, np.array([1.0, 2.0, 3.0])) == 0.0


def test_significant_drop_and_change() -> None:
    stats = stats_for({1: [20.0] * 10 + [15.0]})
    row = stats.loc[1]
    assert row["prev_price"] == 20.0
    assert row["change_abs"] == -5.0
    assert row["change_pct"] == pytest.approx(-25.0)
    assert row["reference_price"] == pytest.approx(20.0)
    assert row["drop_pct"] == pytest.approx(-25.0)
    assert row["is_significant_drop"]
    assert row["is_all_time_low"]
    assert row["min_price"] == 15.0 and row["max_price"] == 20.0


def test_small_change_is_not_significant() -> None:
    row = stats_for({1: [20.0] * 10 + [19.0]}).loc[1]
    assert not row["is_significant_drop"]


def test_anomaly_detection_uses_history_before_last_point() -> None:
    base = [20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.1, 19.9]
    spike = stats_for({1: [*base, 30.0]}).loc[1]
    assert spike["is_anomaly"] and spike["zscore"] > 2.5
    normal = stats_for({1: [*base, 20.05]}).loc[1]
    assert not normal["is_anomaly"]


def test_not_enough_history() -> None:
    row = stats_for({1: [20.0, 30.0]}).loc[1]
    assert row["zscore"] == 0.0 and not row["is_anomaly"] and row["trend"] == "stable"
    single = stats_for({2: [20.0]}).loc[2]
    assert pd.isna(single["prev_price"]) and pd.isna(single["drop_pct"])
    assert not single["is_all_time_low"]


def test_trend_classification() -> None:
    stats = stats_for(
        {
            1: [10 + 0.5 * i for i in range(14)],
            2: [20 - 0.5 * i for i in range(14)],
            3: [15.0] * 14,
        }
    )
    assert stats.loc[1, "trend"] == "hausse"
    assert stats.loc[2, "trend"] == "baisse"
    assert stats.loc[3, "trend"] == "stable"
    assert stats.loc[1, "trend_pct_per_day"] > 0


def test_trend_window_ignores_old_points() -> None:
    # Forte baisse ancienne puis plateau sur la fenêtre de 14 jours -> stable
    prices = [100 - 5 * i for i in range(10)] + [50.0] * 20
    assert stats_for({1: prices}).loc[1, "trend"] == "stable"


def test_target_price() -> None:
    history = history_from_prices({1: [20.0] * 5, 2: [20.0] * 5})
    stats = compute_product_stats(
        history, SETTINGS, targets={"https://a.test/1": 25.0, "https://a.test/2": 10.0}
    ).set_index("product_id")
    assert stats.loc[1, "target_reached"] and not stats.loc[2, "target_reached"]


def test_top_drops_and_rises() -> None:
    stats = compute_product_stats(
        history_from_prices(
            {1: [20.0] * 6 + [10.0], 2: [20.0] * 6 + [18.0], 3: [20.0] * 6 + [25.0]}
        ),
        SETTINGS,
    )
    drops = top_drops(stats, 5)
    assert list(drops["product_id"]) == [1, 2]
    assert list(top_rises(stats, 5)["product_id"]) == [3]
    assert len(top_drops(stats, 1)) == 1
    empty = compute_product_stats(pd.DataFrame(), SETTINGS)
    assert empty.empty and top_drops(empty).empty and top_rises(empty).empty


def test_flag_anomalies_rolling() -> None:
    base = [20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.1, 19.9]
    flagged = flag_anomalies(history_from_prices({1: [*base, 35.0, *base]}), threshold=2.5)
    assert flagged["is_anomaly"].sum() == 1
    assert flagged.loc[flagged["is_anomaly"], "price"].iloc[0] == 35.0
    empty = flag_anomalies(
        history_from_prices({}).reindex(columns=["product_id", "collected_at", "price"])
    )
    assert empty.empty and "rolling_z" in empty


def test_category_index() -> None:
    history = pd.concat(
        [
            history_from_prices({1: [10.0, 11.0]}, category="A"),
            history_from_prices({2: [20.0, 18.0]}, category=None),  # type: ignore[arg-type]
        ]
    )
    index = category_index(history).set_index(["category"])
    last = index.groupby(level=0)["index"].last()
    assert last["A"] == pytest.approx(110.0)
    assert last["Non classé"] == pytest.approx(90.0)
    assert category_index(pd.DataFrame()).empty


def test_summarize() -> None:
    history = history_from_prices({1: [20.0] * 6 + [10.0], 2: [20.0] * 7})
    stats = compute_product_stats(history, SETTINGS)
    summary = summarize(stats, history)
    assert summary["n_products"] == 2
    assert summary["n_observations"] == 14
    assert summary["n_significant_drops"] == 1
    assert summary["biggest_drop"]["product_id"] == 1
    assert summary["avg_change_pct"] == pytest.approx(-25.0)
    empty = summarize(pd.DataFrame(), pd.DataFrame())
    assert empty["n_products"] == 0 and empty["biggest_drop"] is None
    no_ref = summarize(
        compute_product_stats(history_from_prices({1: [5.0]}), SETTINGS),
        history_from_prices({1: [5.0]}),
    )
    assert no_ref["avg_change_pct"] == 0.0
