"""Formatage des nombres et dates selon la langue (``fr`` ou ``en``).

- ``fr`` : ``1 234,50``, ``51,77 £``, ``−12,3 %``, ``26/10/2026``
- ``en`` : ``1,234.50``, ``£51.77``, ``−12.3%``, ``26 Oct 2026``
"""

from __future__ import annotations

from typing import Any

import pandas as pd

CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "CHF": "CHF", "JPY": "¥"}
_NBSP = " "
_EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _is_missing(value: Any) -> bool:
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def fmt_number(value: Any, decimals: int = 2, lang: str = "fr") -> str:
    """``1234.5`` → ``"1 234,50"`` (fr, espace insécable) ou ``"1,234.50"`` (en)."""
    if _is_missing(value):
        return "—"
    text = f"{float(value):,.{decimals}f}"
    if lang == "en":
        return text
    return text.replace(",", _NBSP).replace(".", ",")


def fmt_money(value: Any, currency: str | None = "EUR", lang: str = "fr") -> str:
    """``51.77, "GBP"`` → ``"51,77 £"`` (fr) ou ``"£51.77"`` (en)."""
    if _is_missing(value):
        return "—"
    code = currency or ""
    symbol = CURRENCY_SYMBOLS.get(code, code)
    number = fmt_number(value, lang=lang)
    if lang != "en":
        return f"{number}{_NBSP}{symbol}".strip()
    # Symbole en préfixe (« £51.77 ») ; code alphabétique séparé (« CHF 12.00 »)
    if len(symbol) == 1:
        return f"{symbol}{number}"
    return f"{symbol} {number}".strip()


def fmt_pct(value: Any, decimals: int = 1, signed: bool = True, lang: str = "fr") -> str:
    """``-12.34`` → ``"−12,3 %"`` (fr) ou ``"−12.3%"`` (en), vrai signe moins."""
    if _is_missing(value):
        return "—"
    number = float(value)
    sign = ""
    if signed and number > 0:
        sign = "+"
    elif signed and number < 0:
        sign = "−"
    body = fmt_number(abs(number) if signed else number, decimals, lang=lang)
    return f"{sign}{body}%" if lang == "en" else f"{sign}{body}{_NBSP}%"


def fmt_date(value: Any, with_time: bool = False, lang: str = "fr") -> str:
    """``26/10/2026`` (fr) ou ``26 Oct 2026`` (en) ; ``… 06:00 UTC`` avec l'heure."""
    if _is_missing(value):
        return "—"
    ts = pd.Timestamp(value)
    if lang == "en":
        text = f"{ts.day} {_EN_MONTHS[ts.month - 1]} {ts.year}"
    else:
        text = ts.strftime("%d/%m/%Y")
    return f"{text} {ts:%H:%M} UTC" if with_time else text
