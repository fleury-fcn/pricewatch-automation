"""Formatage des nombres à la française (montants, pourcentages, dates)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "CHF": "CHF", "JPY": "¥"}
_NBSP = " "


def _is_missing(value: Any) -> bool:
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def fmt_number(value: Any, decimals: int = 2) -> str:
    """``1234.5`` → ``"1 234,50"`` (espace insécable pour les milliers)."""
    if _is_missing(value):
        return "—"
    text = f"{float(value):,.{decimals}f}"
    return text.replace(",", _NBSP).replace(".", ",")


def fmt_money(value: Any, currency: str | None = "EUR") -> str:
    """``51.77, "GBP"`` → ``"51,77 £"``."""
    if _is_missing(value):
        return "—"
    symbol = CURRENCY_SYMBOLS.get(currency or "", currency or "")
    return f"{fmt_number(value)}{_NBSP}{symbol}".strip()


def fmt_pct(value: Any, decimals: int = 1, signed: bool = True) -> str:
    """``-12.34`` → ``"−12,3 %"`` (vrai signe moins typographique)."""
    if _is_missing(value):
        return "—"
    number = float(value)
    sign = ("+" if number > 0 else "−" if number < 0 else "") if signed else ""
    return f"{sign}{fmt_number(abs(number) if signed else number, decimals)}{_NBSP}%"


def fmt_date(value: Any, with_time: bool = False) -> str:
    """Date au format ``26/10/2026`` (``26/10/2026 06:00 UTC`` avec l'heure)."""
    if _is_missing(value):
        return "—"
    ts = pd.Timestamp(value) if not isinstance(value, datetime) else pd.Timestamp(value)
    return ts.strftime("%d/%m/%Y %H:%M UTC" if with_time else "%d/%m/%Y")
