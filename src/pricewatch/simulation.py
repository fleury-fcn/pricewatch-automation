"""Simulation déterministe de variations de prix (mode démo).

books.toscrape.com est un bac à sable dont les prix ne changent jamais. Pour démontrer
l'analyse (tendances, promotions, anomalies), on applique au prix réel collecté une
variation **déterministe** : le prix simulé d'un produit dépend uniquement de son URL, de
son prix de base et de la date. Deux exécutions le même jour donnent donc le même prix, et
l'historique généré a posteriori (backfill) est cohérent avec les exécutions quotidiennes.

Composantes : saisonnalité sinusoïdale propre à chaque produit, bruit léger, promotions
ponctuelles (-10 à -35 % pendant 3 à 7 jours) et rares pics anormaux (+30 à +60 %).
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd

PROMO_BLOCK_DAYS = 21
PROMO_PROBABILITY = 0.35
SPIKE_PROBABILITY = 0.012


def _rng(*parts: object) -> random.Random:
    """Générateur pseudo-aléatoire stable (indépendant de PYTHONHASHSEED)."""
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def simulated_price(base_price: float, key: str, day: date) -> float:
    """Prix simulé d'un produit pour une date donnée.

    Args:
        base_price: Prix réel observé (sert de prix de référence).
        key: Identifiant stable du produit (URL).
        day: Date de l'observation.

    Returns:
        Le prix simulé, arrondi au centime (toujours strictement positif).
    """
    profile = _rng(key, "profile")
    period = profile.uniform(18, 60)
    phase = profile.uniform(0, 2 * math.pi)
    amplitude = profile.uniform(0.02, 0.08)
    drift = profile.uniform(-0.0015, 0.0015)  # dérive lente (par jour)

    t = day.toordinal()
    origin = date(2026, 1, 1).toordinal()
    factor = 1 + amplitude * math.sin(2 * math.pi * t / period + phase)
    factor *= 1 + max(-0.25, min(0.25, drift * (t - origin) / 10))
    factor *= 1 + _rng(key, "noise", t).gauss(0, 0.008)

    block = t // PROMO_BLOCK_DAYS
    promo = _rng(key, "promo", block)
    if promo.random() < PROMO_PROBABILITY:
        start = block * PROMO_BLOCK_DAYS + promo.randint(0, PROMO_BLOCK_DAYS - 8)
        duration = promo.randint(3, 7)
        if start <= t < start + duration:
            factor *= 1 - promo.uniform(0.10, 0.35)

    spike = _rng(key, "spike", t)
    if spike.random() < SPIKE_PROBABILITY:
        factor *= 1 + spike.uniform(0.30, 0.60)

    return max(0.01, round(base_price * factor, 2))


def apply_simulation(
    data: pd.DataFrame,
    sources: set[str],
    rates: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Applique la simulation aux observations des ``sources`` indiquées.

    Args:
        data: DataFrame nettoyé (schéma ``CleanProduct``).
        sources: Noms des sources pour lesquelles simuler les prix.
        rates: Taux de change vers la devise de base (pour recalculer ``price_base``).

    Returns:
        Une copie du DataFrame avec ``price`` (et ``price_base``) simulés.
    """
    df = data.copy()
    if df.empty or not sources:
        return df
    mask = df["source"].isin(sources)
    df["price"] = df["price"].astype("float64")
    df.loc[mask, "price"] = [
        simulated_price(float(price), str(url), pd.Timestamp(ts).date())
        for price, url, ts in zip(
            df.loc[mask, "price"], df.loc[mask, "url"], df.loc[mask, "collected_at"], strict=True
        )
    ]
    if rates is not None:
        df.loc[mask, "price_base"] = [
            round(p * rates[c], 2) if c in rates else None
            for p, c in zip(df.loc[mask, "price"], df.loc[mask, "currency"], strict=True)
        ]
    return df


def generate_history(
    catalogue: pd.DataFrame,
    days: int,
    end: date | None = None,
    rates: Mapping[str, float] | None = None,
    hour: int = 6,
) -> pd.DataFrame:
    """Génère un historique simulé de ``days`` jours pour un catalogue de produits.

    Args:
        catalogue: Une ligne par produit (schéma ``CleanProduct``) ; ``price`` = prix de base.
        days: Nombre de jours d'historique (date de fin incluse).
        end: Dernier jour (aujourd'hui par défaut).
        rates: Taux de change pour ``price_base``.
        hour: Heure UTC des observations.

    Returns:
        Un DataFrame au schéma ``CleanProduct`` avec ``len(catalogue) * days`` lignes.
    """
    if days < 1:
        raise ValueError("days doit être >= 1")
    end = end or datetime.now(UTC).date()
    frames = []
    for offset in range(days - 1, -1, -1):
        day = end - timedelta(days=offset)
        frame = catalogue.copy()
        frame["collected_at"] = pd.Timestamp(datetime.combine(day, time(hour), tzinfo=UTC))
        frames.append(frame)
    history = pd.concat(frames, ignore_index=True)
    return apply_simulation(history, set(history["source"]), rates)
