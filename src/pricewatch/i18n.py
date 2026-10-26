"""Textes traduits (français / anglais) des rapports, graphiques et alertes.

Usage : ``tr("en", "kpi_products")`` ou, pour un texte paramétré,
``tr("fr", "summary_counts", drops=3, anomalies=1, ...)``.
"""

from __future__ import annotations

from typing import Any, Literal

Lang = Literal["fr", "en"]
LANGUAGES: tuple[Lang, ...] = ("fr", "en")
DEFAULT_LANG: Lang = "fr"

TEXTS: dict[str, dict[str, str]] = {
    "fr": {
        # rapport — en-tête et indicateurs
        "html_lang": "fr",
        "report_title": "PriceWatch — Rapport de suivi des prix",
        "generated_on": "Généré le {date}",
        "period": "période du {start} au {end}",
        "switch_label": "English version",
        "demo_title": "Mode démo.",
        "demo_body": (
            "La source books.toscrape.com est un bac à sable aux prix fixes : les variations "
            "présentées sont simulées de façon déterministe à partir des prix réels collectés."
        ),
        "meta_description": (
            "Rapport PriceWatch du {date} : {n} produits suivis, baisses de prix, "
            "anomalies et alertes."
        ),
        "kpi_products": "produits suivis",
        "kpi_drops": "baisses significatives (≤ −{threshold} %)",
        "kpi_drops_short": "Baisses significatives",
        "kpi_anomalies": "prix anormaux (z-score)",
        "kpi_anomalies_short": "Anomalies",
        "kpi_avg_gap": "écart moyen vs moyenne récente",
        "kpi_avg_gap_short": "Écart moyen",
        "kpi_products_short": "Produits suivis",
        "kpi_alerts": "alertes (dernière exécution)",
        # sections
        "section_summary": "Résumé exécutif",
        "section_drops": "Meilleures baisses",
        "section_alerts": "Alertes de la dernière exécution",
        "section_rises": "Plus fortes hausses",
        "section_products": "Tous les produits",
        "section_method": "Méthodologie",
        "no_drops": "Aucune baisse par rapport à la moyenne récente.",
        "no_alerts": "Aucune alerte émise.",
        "show_table": "Afficher le tableau complet ({n} produits)",
        # colonnes
        "col_product": "Produit",
        "col_category": "Catégorie",
        "col_price": "Prix",
        "col_recent_avg": "Moyenne récente",
        "col_average": "Moyenne",
        "col_gap": "Écart",
        "col_vs_prev": "Vs veille",
        "col_trend": "Tendance",
        "col_type": "Type",
        "col_message": "Message",
        "col_date": "Date",
        "col_min": "Min",
        "col_max": "Max",
        "col_zscore": "z-score",
        "col_obs": "Obs.",
        # méthodologie
        "method": (
            "Collecte HTTP respectueuse (robots.txt, délai entre requêtes, User-Agent identifié, "
            "nouvelles tentatives avec backoff exponentiel). Prix nettoyés (devises, séparateurs, "
            "doublons) et validés par un schéma pydantic, puis stockés dans SQLite via SQLAlchemy. "
            "Écart : dernier prix comparé à la moyenne des observations de la fenêtre de tendance. "
            "Anomalie : valeur absolue du z-score du dernier prix, calculé contre l'historique "
            "antérieur, au-dessus du seuil. Tendance : pente d'une régression linéaire sur la "
            "fenêtre."
        ),
        "footer_source": "code source",
        "footer_license": "licence MIT",
        "page": "Page",
        "pdf_subject": "Rapport de suivi des prix",
        # résumé exécutif
        "summary_empty": "Aucune donnée de prix disponible : lancez d'abord une collecte.",
        "summary_scope": "{n} produits suivis, {obs} observations du {start} au {end}.",
        "summary_moves": (
            "Écart moyen du dernier prix à la moyenne récente : {avg} ; {drops} produits en "
            "baisse et {rises} en hausse depuis la veille."
        ),
        "summary_biggest": "Plus forte baisse : « {title} » à {price} ({pct} vs {ref}).",
        "summary_counts": (
            "{drops} baisse(s) significative(s) (≤ −{threshold} %), {anomalies} prix "
            "anormal(aux) (z-score), {targets} prix cible(s) atteint(s), {lows} plus bas "
            "historique(s)."
        ),
        "summary_alerts": "{n} alerte(s) émise(s) lors de la dernière exécution.",
        # graphiques
        "chart_category": "Indice de prix par catégorie (base 100, médiane des produits)",
        "chart_drops": "Meilleures baisses vs moyenne récente",
        "chart_drops_pct": "Meilleures baisses vs moyenne récente (%)",
        "chart_history_pdf": "Historique des plus fortes baisses (trait gris : moyenne de référence)",
        "chart_history": "Historique des plus fortes baisses (gris : moyenne de référence)",
        "chart_prices": "Historique des prix",
        "chart_distribution": "Écart du dernier prix à la moyenne récente",
        "chart_distribution_pct": "Écart du dernier prix à la moyenne récente (%)",
        "threshold": "seuil",
        "axis_products": "produits",
        "legend_price": "prix",
        "legend_anomaly": "anomalie",
        "hover_gap": "Écart",
        "hover_price": "Prix",
        "hover_average": "Moyenne",
        "hover_anomaly": "Anomalie",
        "other": "Autres",
        "uncategorised": "Non classé",
        # tendances et alertes
        "trend_hausse": "hausse",
        "trend_baisse": "baisse",
        "trend_stable": "stable",
        "kind_target_reached": "Prix cible atteint",
        "kind_price_drop": "Baisse significative",
        "kind_anomaly": "Prix anormal",
        "alerts_header": "{title} — {n} alerte(s)",
        "alerts_subject": "[PriceWatch] {n} alerte(s) de prix",
        "msg_target_reached": "« {title} » est à {price} (cible {ref}).",
        "msg_price_drop": "« {title} » baisse de {pct} : {price} contre {ref} en moyenne récente.",
        "msg_anomaly_z": "« {title} » : prix {direction} ({price}, z-score {z}).",
        "msg_anomaly": "« {title} » : prix {direction} ({price} contre {ref} en moyenne récente).",
        "msg_anomaly_plain": "« {title} » : prix {direction} ({price}).",
        "dir_high": "anormalement haut",
        "dir_low": "anormalement bas",
        "dir_unknown": "anormal",
    },
    "en": {
        "html_lang": "en",
        "report_title": "PriceWatch — Price Tracking Report",
        "generated_on": "Generated on {date}",
        "period": "period {start} to {end}",
        "switch_label": "Version française",
        "demo_title": "Demo mode.",
        "demo_body": (
            "The books.toscrape.com source is a sandbox with fixed prices: the price movements "
            "shown are simulated deterministically from the real prices collected."
        ),
        "meta_description": (
            "PriceWatch report for {date}: {n} tracked products, price drops, anomalies "
            "and alerts."
        ),
        "kpi_products": "tracked products",
        "kpi_drops": "significant drops (≤ −{threshold}%)",
        "kpi_drops_short": "Significant drops",
        "kpi_anomalies": "abnormal prices (z-score)",
        "kpi_anomalies_short": "Anomalies",
        "kpi_avg_gap": "average gap vs recent mean",
        "kpi_avg_gap_short": "Average gap",
        "kpi_products_short": "Tracked products",
        "kpi_alerts": "alerts (latest run)",
        "section_summary": "Executive summary",
        "section_drops": "Top price drops",
        "section_alerts": "Alerts from the latest run",
        "section_rises": "Biggest increases",
        "section_products": "All products",
        "section_method": "Methodology",
        "no_drops": "No drop compared with the recent mean.",
        "no_alerts": "No alert raised.",
        "show_table": "Show the full table ({n} products)",
        "col_product": "Product",
        "col_category": "Category",
        "col_price": "Price",
        "col_recent_avg": "Recent mean",
        "col_average": "Mean",
        "col_gap": "Gap",
        "col_vs_prev": "Vs previous",
        "col_trend": "Trend",
        "col_type": "Type",
        "col_message": "Message",
        "col_date": "Date",
        "col_min": "Min",
        "col_max": "Max",
        "col_zscore": "z-score",
        "col_obs": "Obs.",
        "method": (
            "Polite HTTP collection (robots.txt, delay between requests, identified User-Agent, "
            "retries with exponential backoff). Prices are cleaned (currencies, separators, "
            "duplicates), validated against a pydantic schema and stored in SQLite through "
            "SQLAlchemy. Gap: latest price compared with the mean of the observations in the "
            "trend window. Anomaly: absolute z-score of the latest price, computed against prior "
            "history, above the threshold. Trend: slope of a linear regression over the window."
        ),
        "footer_source": "source code",
        "footer_license": "MIT licence",
        "page": "Page",
        "pdf_subject": "Price tracking report",
        "summary_empty": "No price data available yet: run a collection first.",
        "summary_scope": "{n} tracked products, {obs} observations from {start} to {end}.",
        "summary_moves": (
            "Average gap between the latest price and the recent mean: {avg}; {drops} products "
            "down and {rises} up since the previous observation."
        ),
        "summary_biggest": "Biggest drop: “{title}” at {price} ({pct} vs {ref}).",
        "summary_counts": (
            "{drops} significant drop(s) (≤ −{threshold}%), {anomalies} abnormal price(s) "
            "(z-score), {targets} target price(s) reached, {lows} all-time low(s)."
        ),
        "summary_alerts": "{n} alert(s) raised during the latest run.",
        "chart_category": "Price index by category (base 100, median of products)",
        "chart_drops": "Top drops vs recent mean",
        "chart_drops_pct": "Top drops vs recent mean (%)",
        "chart_history_pdf": "History of the biggest drops (grey line: reference mean)",
        "chart_history": "History of the biggest drops (grey: reference mean)",
        "chart_prices": "Price history",
        "chart_distribution": "Gap between latest price and recent mean",
        "chart_distribution_pct": "Gap between latest price and recent mean (%)",
        "threshold": "threshold",
        "axis_products": "products",
        "legend_price": "price",
        "legend_anomaly": "anomaly",
        "hover_gap": "Gap",
        "hover_price": "Price",
        "hover_average": "Mean",
        "hover_anomaly": "Anomaly",
        "other": "Other",
        "uncategorised": "Uncategorised",
        "trend_hausse": "rising",
        "trend_baisse": "falling",
        "trend_stable": "stable",
        "kind_target_reached": "Target price reached",
        "kind_price_drop": "Significant drop",
        "kind_anomaly": "Abnormal price",
        "alerts_header": "{title} — {n} alert(s)",
        "alerts_subject": "[PriceWatch] {n} price alert(s)",
        "msg_target_reached": "“{title}” is at {price} (target {ref}).",
        "msg_price_drop": "“{title}” is down {pct}: {price} vs a recent mean of {ref}.",
        "msg_anomaly_z": "“{title}”: {direction} price ({price}, z-score {z}).",
        "msg_anomaly": "“{title}”: {direction} price ({price} vs a recent mean of {ref}).",
        "msg_anomaly_plain": "“{title}”: {direction} price ({price}).",
        "dir_high": "abnormally high",
        "dir_low": "abnormally low",
        "dir_unknown": "abnormal",
    },
}


def check_lang(lang: str) -> Lang:
    """Valide un code de langue.

    Raises:
        ValueError: Si la langue n'est pas prise en charge.
    """
    if lang not in TEXTS:
        raise ValueError(
            f"Langue non prise en charge : {lang} (disponibles : {', '.join(LANGUAGES)})"
        )
    return lang  # type: ignore[return-value]


def tr(lang: str, key: str, **params: Any) -> str:
    """Texte traduit ; les ``params`` remplissent les ``{champs}`` du gabarit."""
    text = TEXTS[check_lang(lang)][key]
    return text.format(**params) if params else text


def texts(lang: str) -> dict[str, str]:
    """Tous les textes d'une langue (pour les templates Jinja2)."""
    return TEXTS[check_lang(lang)]


def trend_label(lang: str, trend: str) -> str:
    """Libellé traduit d'une tendance (``hausse`` / ``baisse`` / ``stable``)."""
    key = f"trend_{trend}"
    return TEXTS[check_lang(lang)].get(key, trend)


def kind_label(lang: str, kind: str) -> str:
    """Libellé traduit d'un type d'alerte."""
    return TEXTS[check_lang(lang)].get(f"kind_{kind}", kind)
