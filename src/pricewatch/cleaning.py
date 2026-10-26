"""Nettoyage et validation des enregistrements bruts avec pandas + pydantic.

Étapes : construction du DataFrame → normalisation des textes, prix, devises et
disponibilité → suppression des doublons → rejet des valeurs manquantes/aberrantes →
validation ligne à ligne par :class:`~pricewatch.schemas.CleanProduct`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
from pydantic import ValidationError

from pricewatch.collectors.base import RawProduct
from pricewatch.config import CleaningSettings
from pricewatch.logging_setup import get_logger
from pricewatch.schemas import CleaningReport, CleanProduct, Rejection

logger = get_logger(__name__)

CLEAN_COLUMNS = list(CleanProduct.model_fields)

CURRENCY_SYMBOLS: dict[str, str] = {"£": "GBP", "€": "EUR", "$": "USD", "¥": "JPY", "₹": "INR"}
CURRENCY_CODES = frozenset({"GBP", "EUR", "USD", "JPY", "INR", "CHF", "CAD", "AUD"})
# Artefacts d'encodage fréquents (UTF-8 lu comme Latin-1) : « Â£ » → « £ », « â‚¬ » → « € »
_MOJIBAKE = {"Â£": "£", "â‚¬": "€", "Â": ""}
_NUMBER = re.compile(r"\d[\d\s.,'  ]*")
_STOCK_QTY = re.compile(r"(\d+)\s*(?:available|disponibles?|in stock|en stock)", re.IGNORECASE)
_OUT_OF_STOCK = re.compile(r"out of stock|rupture|indisponible|épuisé|unavailable", re.IGNORECASE)
_IN_STOCK = re.compile(r"in stock|en stock|disponible|available", re.IGNORECASE)


def _fix_mojibake(text: str) -> str:
    for bad, good in _MOJIBAKE.items():
        text = text.replace(bad, good)
    return text


def _parse_number(token: str) -> float | None:
    """Convertit « 1 234,56 », « 1,234.56 », « 51.77 » ou « 1'000 » en float."""
    token = re.sub(r"[\s'  ]", "", token).strip(".,")
    if not token:
        return None
    if "," in token and "." in token:
        # Le séparateur le plus à droite est le séparateur décimal.
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        token = token.replace(thousands, "").replace(decimal, ".")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        # « 1,234 » = milliers ; « 12,50 » = décimales
        token = (
            token.replace(",", "") if len(tail) == 3 and head else f"{head.replace(',', '')}.{tail}"
        )
    elif token.count(".") > 1:
        token = token.replace(".", "")  # « 1.234.567 » : points = séparateurs de milliers
    try:
        return float(token)
    except ValueError:
        return None


def parse_price(text: str | None) -> tuple[float | None, str | None]:
    """Extrait le montant et la devise ISO d'un prix textuel.

    Examples:
        >>> parse_price("£51.77")
        (51.77, 'GBP')
        >>> parse_price("1 234,50 €")
        (1234.5, 'EUR')
        >>> parse_price("USD 1,299.00")
        (1299.0, 'USD')

    Returns:
        ``(montant, devise)`` ; chaque élément vaut ``None`` s'il est introuvable.
    """
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None, None
    text = _fix_mojibake(str(text))
    currency = next((code for sym, code in CURRENCY_SYMBOLS.items() if sym in text), None)
    if currency is None:
        upper = text.upper()
        currency = next((code for code in CURRENCY_CODES if code in upper), None)
    match = _NUMBER.search(text)
    amount = _parse_number(match.group()) if match else None
    return amount, currency


def parse_availability(text: str | None) -> tuple[bool | None, int | None]:
    """Interprète un texte de disponibilité.

    Examples:
        >>> parse_availability("In stock (22 available)")
        (True, 22)
        >>> parse_availability("Out of stock")
        (False, 0)
    """
    if not text or not isinstance(text, str):
        return None, None
    if _OUT_OF_STOCK.search(text):
        return False, 0
    qty_match = _STOCK_QTY.search(text)
    qty = int(qty_match.group(1)) if qty_match else None
    if qty is not None:
        return qty > 0, qty
    if _IN_STOCK.search(text):
        return True, None
    return None, None


def _normalize_text(series: pd.Series) -> pd.Series:
    return (
        series.astype("string").str.replace(r"\s+", " ", regex=True).str.strip().replace("", pd.NA)
    )


@dataclass
class CleaningResult:
    """DataFrame validé et bilan du nettoyage."""

    data: pd.DataFrame
    report: CleaningReport


def to_dataframe(products: Iterable[RawProduct]) -> pd.DataFrame:
    """Convertit des :class:`RawProduct` en DataFrame brut."""
    rows = [p.to_dict() for p in products]
    columns = list(RawProduct.__dataclass_fields__)
    return pd.DataFrame(rows, columns=columns)


def clean_products(
    raw: pd.DataFrame | Iterable[RawProduct],
    settings: CleaningSettings | None = None,
) -> CleaningResult:
    """Nettoie et valide des enregistrements bruts.

    Args:
        raw: DataFrame brut (colonnes de :class:`RawProduct`) ou itérable de RawProduct.
        settings: Paramètres de nettoyage (devises, taux, bornes).

    Returns:
        Un :class:`CleaningResult` dont ``data`` respecte le schéma ``CleanProduct``.
    """
    settings = settings or CleaningSettings()
    df = raw.copy() if isinstance(raw, pd.DataFrame) else to_dataframe(raw)
    report = CleaningReport(n_input=len(df))
    if df.empty:
        return CleaningResult(pd.DataFrame(columns=CLEAN_COLUMNS), report)

    for column in ("source", "url", "title", "category", "external_id", "image_url"):
        if column in df:
            df[column] = _normalize_text(df[column])

    prices = df["price_raw"].map(parse_price)
    df["price"] = prices.map(lambda pc: pc[0]).astype("Float64")
    df["currency"] = prices.map(lambda pc: pc[1]).fillna(settings.default_currency)
    stock = df["availability_raw"].map(parse_availability)
    df["in_stock"] = stock.map(lambda s: s[0])
    df["stock_qty"] = stock.map(lambda s: s[1])
    rates = settings.exchange_rates
    df["price_base"] = [
        round(float(p) * rates[c], 2) if pd.notna(p) and c in rates else None
        for p, c in zip(df["price"], df["currency"], strict=True)
    ]
    df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)

    # Doublons : même URL dans une même collecte -> on garde l'observation la plus récente.
    df = df.sort_values("collected_at", kind="stable")
    duplicated = df.duplicated(subset=["url"], keep="last") & df["url"].notna()
    report.n_duplicates = int(duplicated.sum())
    df = df.loc[~duplicated]

    rows: list[dict[str, object]] = []
    for record in df.to_dict(orient="records"):
        reason = _missing_reason(record, settings)
        if reason is None:
            try:
                payload = {k: _none_if_na(record.get(k)) for k in CLEAN_COLUMNS}
                rows.append(CleanProduct.model_validate(payload).model_dump())
                continue
            except ValidationError as exc:
                reason = "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
                )
        report.rejections.append(Rejection(url=_none_if_na(record.get("url")), reason=reason))

    report.n_rejected = len(report.rejections)
    report.n_valid = len(rows)
    for rejection in report.rejections:
        logger.warning("Enregistrement rejeté (%s) : %s", rejection.url, rejection.reason)
    logger.info(
        "Nettoyage : %d entrées, %d doublons, %d rejetés, %d valides",
        report.n_input,
        report.n_duplicates,
        report.n_rejected,
        report.n_valid,
    )
    data = pd.DataFrame(rows, columns=CLEAN_COLUMNS)
    if not data.empty:
        data["collected_at"] = pd.to_datetime(data["collected_at"], utc=True)
    return CleaningResult(data.reset_index(drop=True), report)


def _none_if_na(value: object) -> object:
    try:
        return None if pd.isna(value) else value  # type: ignore[arg-type]
    except (TypeError, ValueError):  # listes/objets non scalaires
        return value


def _missing_reason(record: dict[str, object], settings: CleaningSettings) -> str | None:
    for field in ("url", "title"):
        if _none_if_na(record.get(field)) is None:
            return f"{field} manquant"
    price = _none_if_na(record.get("price"))
    if price is None:
        return f"prix illisible : {record.get('price_raw')!r}"
    if not 0 < float(price) <= settings.max_price:  # type: ignore[arg-type]
        return f"prix hors bornes : {price}"
    return None
