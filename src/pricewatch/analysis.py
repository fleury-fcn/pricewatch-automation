"""Analyse des historiques : variations, tendances, anomalies (z-score), baisses significatives."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from pricewatch.config import AnalysisSettings

STABLE_TREND_PCT = 0.10  # |pente| < 0,10 %/jour => tendance stable

STATS_COLUMNS = [
    "product_id",
    "title",
    "category",
    "url",
    "currency",
    "n_obs",
    "first_at",
    "last_at",
    "last_price",
    "prev_price",
    "change_abs",
    "change_pct",
    "reference_price",
    "drop_pct",
    "min_price",
    "max_price",
    "is_all_time_low",
    "trend_pct_per_day",
    "trend",
    "zscore",
    "is_anomaly",
    "is_significant_drop",
    "target_price",
    "target_reached",
]


def zscore(value: float, sample: np.ndarray) -> float:
    """z-score de ``value`` par rapport à ``sample`` (0 si l'écart-type est nul)."""
    if sample.size < 2:
        return 0.0
    std = float(np.std(sample, ddof=1))
    if std == 0 or np.isnan(std):
        return 0.0
    return float((value - float(np.mean(sample))) / std)


def trend_slope_pct(times: pd.Series, prices: np.ndarray) -> float:
    """Pente d'une régression linéaire prix ~ temps, en % du prix moyen par jour."""
    if len(prices) < 2:
        return 0.0
    days = (times - times.iloc[0]).dt.total_seconds().to_numpy() / 86_400
    if np.ptp(days) == 0:
        return 0.0
    slope = float(np.polyfit(days, prices, 1)[0])
    mean = float(np.mean(prices))
    return 0.0 if mean == 0 else slope / mean * 100


def _classify_trend(slope_pct: float) -> str:
    if slope_pct > STABLE_TREND_PCT:
        return "hausse"
    if slope_pct < -STABLE_TREND_PCT:
        return "baisse"
    return "stable"


def _product_stats(
    group: pd.DataFrame, settings: AnalysisSettings, target: float | None
) -> dict[str, Any]:
    group = group.sort_values("collected_at")
    prices = group["price"].to_numpy(dtype=float)
    last_at = group["collected_at"].iloc[-1]
    last = prices[-1]
    previous = prices[:-1]
    prev_price = float(previous[-1]) if previous.size else None

    window_start = last_at - timedelta(days=settings.trend_window_days)
    in_window = (group["collected_at"] >= window_start).to_numpy()
    window_prev = prices[:-1][in_window[:-1]]
    reference = float(np.mean(window_prev)) if window_prev.size else None
    drop_pct = (last - reference) / reference * 100 if reference else None

    enough = len(prices) >= settings.min_history_points
    z = zscore(last, previous) if enough else 0.0
    slope = (
        trend_slope_pct(group["collected_at"][in_window], prices[in_window])
        if in_window.sum() >= settings.min_history_points
        else 0.0
    )
    latest = group.iloc[-1]
    return {
        "product_id": latest["product_id"],
        "title": latest["title"],
        "category": latest.get("category"),
        "url": latest["url"],
        "currency": latest["currency"],
        "n_obs": len(prices),
        "first_at": group["collected_at"].iloc[0],
        "last_at": last_at,
        "last_price": float(last),
        "prev_price": prev_price,
        "change_abs": None if prev_price is None else round(float(last - prev_price), 2),
        "change_pct": None if not prev_price else (last - prev_price) / prev_price * 100,
        "reference_price": reference,
        "drop_pct": drop_pct,
        "min_price": float(prices.min()),
        "max_price": float(prices.max()),
        "is_all_time_low": bool(previous.size and last < previous.min()),
        "trend_pct_per_day": slope,
        "trend": _classify_trend(slope),
        "zscore": z,
        "is_anomaly": bool(enough and abs(z) >= settings.zscore_threshold),
        "is_significant_drop": bool(
            drop_pct is not None and drop_pct <= -settings.significant_drop_pct
        ),
        "target_price": target,
        "target_reached": bool(target is not None and last <= target),
    }


def compute_product_stats(
    history: pd.DataFrame,
    settings: AnalysisSettings | None = None,
    targets: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Calcule les indicateurs par produit à partir de l'historique.

    Args:
        history: Historique (colonnes ``product_id, title, url, price, currency,
            collected_at`` au minimum), tel que renvoyé par le dépôt.
        settings: Seuils d'analyse.
        targets: Prix cibles par URL (watchlist).

    Returns:
        Un DataFrame avec une ligne par produit (colonnes :data:`STATS_COLUMNS`).
    """
    settings = settings or AnalysisSettings()
    targets = targets or {}
    if history.empty:
        return pd.DataFrame(columns=STATS_COLUMNS)
    rows = [
        _product_stats(group, settings, targets.get(str(group["url"].iloc[0])))
        for _, group in history.groupby("product_id", sort=False)
    ]
    return pd.DataFrame(rows, columns=STATS_COLUMNS)


def top_drops(stats: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Les ``n`` plus fortes baisses par rapport au prix de référence (moyenne de fenêtre)."""
    if stats.empty:
        return stats
    drops = stats[stats["drop_pct"].notna() & (stats["drop_pct"] < 0)]
    return drops.sort_values("drop_pct").head(n).reset_index(drop=True)


def top_rises(stats: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Les ``n`` plus fortes hausses par rapport au prix de référence."""
    if stats.empty:
        return stats
    rises = stats[stats["drop_pct"].notna() & (stats["drop_pct"] > 0)]
    return rises.sort_values("drop_pct", ascending=False).head(n).reset_index(drop=True)


def flag_anomalies(history: pd.DataFrame, threshold: float = 2.5, window: int = 14) -> pd.DataFrame:
    """Marque chaque observation dont le z-score glissant dépasse ``threshold``.

    Le z-score est calculé contre les ``window`` observations **précédentes** du même
    produit, pour qu'un pic n'augmente pas l'écart-type qui sert à le détecter.

    Returns:
        Copie de ``history`` avec les colonnes ``rolling_z`` et ``is_anomaly``.
    """
    df = history.sort_values(["product_id", "collected_at"]).copy()
    if df.empty:
        df["rolling_z"] = pd.Series(dtype=float)
        df["is_anomaly"] = pd.Series(dtype=bool)
        return df
    grouped = df.groupby("product_id")["price"]
    mean = grouped.transform(lambda s: s.shift(1).rolling(window, min_periods=3).mean())
    std = grouped.transform(lambda s: s.shift(1).rolling(window, min_periods=3).std())
    z = (df["price"] - mean) / std.replace(0, np.nan)
    df["rolling_z"] = z.fillna(0.0)
    df["is_anomaly"] = df["rolling_z"].abs() >= threshold
    return df


def category_index(history: pd.DataFrame, unknown_label: str = "Non classé") -> pd.DataFrame:
    """Indice de prix quotidien par catégorie (base 100 au premier jour, médiane des produits).

    Returns:
        DataFrame long ``date, category, index``.
    """
    if history.empty:
        return pd.DataFrame(columns=["date", "category", "index"])
    df = history.reset_index(drop=True)
    df = df.assign(
        date=df["collected_at"].dt.tz_convert("UTC").dt.floor("D"),
        category=df["category"].fillna(unknown_label),
    )
    # Prix relatif de chaque produit vs sa première observation, puis moyenne par catégorie
    first = df.sort_values("collected_at").groupby("product_id")["price"].transform("first")
    df["relative"] = df["price"] / first * 100
    # Médiane plutôt que moyenne : un pic isolé sur un produit ne déforme pas l'indice.
    out = df.groupby(["date", "category"], as_index=False)["relative"].median()
    return out.rename(columns={"relative": "index"})


def summarize(stats: pd.DataFrame, history: pd.DataFrame) -> dict[str, Any]:
    """Indicateurs clés pour le résumé exécutif du rapport."""
    if stats.empty:
        return {
            "n_products": 0,
            "n_observations": 0,
            "period_start": None,
            "period_end": None,
            "n_drops": 0,
            "n_rises": 0,
            "n_significant_drops": 0,
            "n_anomalies": 0,
            "n_targets_reached": 0,
            "n_all_time_lows": 0,
            "avg_change_pct": 0.0,
            "biggest_drop": None,
        }
    biggest = top_drops(stats, 1)
    avg_change = stats["drop_pct"].dropna().mean()
    return {
        "n_products": int(len(stats)),
        "n_observations": int(len(history)),
        "period_start": history["collected_at"].min(),
        "period_end": history["collected_at"].max(),
        "n_drops": int((stats["change_pct"] < 0).sum()),
        "n_rises": int((stats["change_pct"] > 0).sum()),
        "n_significant_drops": int(stats["is_significant_drop"].sum()),
        "n_anomalies": int(stats["is_anomaly"].sum()),
        "n_targets_reached": int(stats["target_reached"].sum()),
        "n_all_time_lows": int(stats["is_all_time_low"].sum()),
        "avg_change_pct": 0.0 if pd.isna(avg_change) else float(avg_change),
        "biggest_drop": None if biggest.empty else biggest.iloc[0].to_dict(),
    }
