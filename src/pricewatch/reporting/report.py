"""Génération du rapport daté, en français et en anglais.

- HTML interactif (graphiques Plotly) via Jinja2 ;
- PDF A4 (graphiques matplotlib) via ReportLab.

Chaque rapport existe dans chaque langue configurée (``report.languages``) :
``pricewatch_report_2026-10-26_fr.pdf``, ``pricewatch_report_2026-10-26_en.pdf``…
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pricewatch import __version__
from pricewatch.alerts import alert_message
from pricewatch.analysis import (
    category_index,
    compute_product_stats,
    flag_anomalies,
    summarize,
    top_drops,
    top_rises,
)
from pricewatch.config import AppConfig
from pricewatch.formatting import fmt_date, fmt_money, fmt_number, fmt_pct
from pricewatch.i18n import DEFAULT_LANG, check_lang, kind_label, texts, tr, trend_label
from pricewatch.logging_setup import get_logger
from pricewatch.reporting import charts
from pricewatch.storage.repository import PriceRepository

logger = get_logger(__name__)


@dataclass
class ReportData:
    """Tout ce qu'il faut pour produire un rapport dans une langue (indépendant du format)."""

    title: str
    generated_at: datetime
    summary: dict[str, Any]
    stats: pd.DataFrame
    history: pd.DataFrame
    drops: pd.DataFrame
    rises: pd.DataFrame
    alerts: pd.DataFrame
    runs: pd.DataFrame
    category_series: pd.DataFrame
    significant_drop_pct: float
    simulated: bool = False
    lang: str = DEFAULT_LANG
    highlights: list[str] = field(default_factory=list)

    @property
    def currency(self) -> str:
        """Devise majoritaire des produits suivis."""
        if self.stats.empty:
            return "EUR"
        return str(self.stats["currency"].mode().iloc[0])


def report_filename(stamp: str, lang: str, fmt: str) -> str:
    """Nom du rapport daté : ``pricewatch_report_<date>_<langue>.<format>``."""
    return f"pricewatch_report_{stamp}_{lang}.{fmt}"


def executive_summary(data: ReportData) -> list[str]:
    """Rédige les points clés du résumé exécutif dans la langue du rapport."""
    lang, s = data.lang, data.summary
    if not s["n_products"]:
        return [tr(lang, "summary_empty")]
    points = [
        tr(
            lang,
            "summary_scope",
            n=s["n_products"],
            obs=fmt_number(s["n_observations"], 0, lang=lang),
            start=fmt_date(s["period_start"], lang=lang),
            end=fmt_date(s["period_end"], lang=lang),
        ),
        tr(
            lang,
            "summary_moves",
            avg=fmt_pct(s["avg_change_pct"], lang=lang),
            drops=s["n_drops"],
            rises=s["n_rises"],
        ),
    ]
    if biggest := s.get("biggest_drop"):
        cur = biggest["currency"]
        points.append(
            tr(
                lang,
                "summary_biggest",
                title=biggest["title"],
                price=fmt_money(biggest["last_price"], cur, lang=lang),
                pct=fmt_pct(biggest["drop_pct"], lang=lang),
                ref=fmt_money(biggest["reference_price"], cur, lang=lang),
            )
        )
    points.append(
        tr(
            lang,
            "summary_counts",
            drops=s["n_significant_drops"],
            threshold=fmt_number(data.significant_drop_pct, 0, lang=lang),
            anomalies=s["n_anomalies"],
            targets=s["n_targets_reached"],
            lows=s["n_all_time_lows"],
        )
    )
    if not data.alerts.empty:
        points.append(tr(lang, "summary_alerts", n=len(data.alerts)))
    return points


def _localize_alerts(alerts: pd.DataFrame, lang: str) -> pd.DataFrame:
    """Réécrit les messages d'alerte dans la langue du rapport (à partir des champs bruts)."""
    if alerts.empty:
        return alerts

    def _opt(value: Any) -> float | None:
        return None if value is None or pd.isna(value) else float(value)

    messages = [
        alert_message(
            a.kind,
            str(a.title),
            float(a.price),
            _opt(a.reference_price),
            _opt(a.change_pct),
            str(a.currency),
            lang,
        )
        for a in alerts.itertuples()
    ]
    return alerts.assign(message=messages)


def build_report_data(
    repo: PriceRepository,
    config: AppConfig,
    generated_at: datetime | None = None,
    lang: str = DEFAULT_LANG,
) -> ReportData:
    """Assemble les données du rapport depuis la base, pour une langue."""
    lang = check_lang(lang)
    history = repo.get_price_history()
    analysis = config.analysis
    stats = compute_product_stats(history, analysis, config.target_prices())
    flagged = flag_anomalies(history, analysis.zscore_threshold, analysis.trend_window_days)
    runs = repo.get_runs(limit=10)
    alerts = repo.get_alerts(limit=200)
    if not runs.empty and not alerts.empty:
        alerts = alerts[alerts["run_id"] == runs.iloc[0]["id"]]
    index = category_index(history, unknown_label=tr(lang, "uncategorised"))
    data = ReportData(
        title=config.report.title or tr(lang, "report_title"),
        generated_at=generated_at or datetime.now(UTC),
        summary=summarize(stats, history),
        stats=stats,
        history=flagged,
        drops=top_drops(stats, config.report.top_n),
        rises=top_rises(stats, config.report.top_n),
        alerts=_localize_alerts(alerts.reset_index(drop=True), lang),
        runs=runs,
        category_series=charts.fold_categories(index, history, lang=lang),
        significant_drop_pct=analysis.significant_drop_pct,
        simulated=any(s.simulate_price_moves for s in config.enabled_sources),
        lang=lang,
    )
    data.highlights = executive_summary(data)
    return data


# -- HTML ------------------------------------------------------------------------
def _jinja(lang: str) -> Environment:
    env = Environment(
        loader=PackageLoader("pricewatch", "reporting/templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(
        money=partial(fmt_money, lang=lang),
        pct=partial(fmt_pct, lang=lang),
        num=partial(fmt_number, lang=lang),
        date=partial(fmt_date, lang=lang),
        kind_label=partial(kind_label, lang),
        trend=partial(trend_label, lang),
    )
    return env


def render_html(data: ReportData, alternates: dict[str, str] | None = None) -> str:
    """Rapport HTML autonome (graphiques Plotly interactifs, plotly.js via CDN).

    Args:
        data: Données du rapport (langue incluse).
        alternates: Liens vers les autres langues, ``{"en": "rapport_en.html"}``.
    """
    lang = data.lang
    figures: list[tuple[str, Any]] = []
    if not data.category_series.empty:
        figures.append(("category", charts.px_category_index(data.category_series, lang)))
    if not data.drops.empty:
        figures.append(("drops", charts.px_top_drops(data.drops, lang)))
        figures.append(
            ("history", charts.px_product_history(data.history, data.drops.head(3), lang))
        )
    if not data.stats.empty and data.stats["drop_pct"].notna().any():
        figures.append(
            (
                "distribution",
                charts.px_change_distribution(data.stats, data.significant_drop_pct, lang),
            )
        )
    html_figures = {
        name: fig.to_html(
            full_html=False,
            include_plotlyjs="cdn" if i == 0 else False,
            config={"displaylogo": False, "responsive": True, "locale": lang},
            div_id=f"chart-{name}",
        )
        for i, (name, fig) in enumerate(figures)
    }
    products = data.stats.sort_values("drop_pct", na_position="last")
    return (
        _jinja(lang)
        .get_template("report.html.j2")
        .render(
            data=data,
            s=data.summary,
            t=texts(lang),
            figures=html_figures,
            products=products,
            alternates=alternates or {},
            version=__version__,
        )
    )


# -- PDF -------------------------------------------------------------------------
def _pdf_text(value: Any) -> str:
    """Échappe le XML et remplace les glyphes absents des polices standard PDF."""
    text = escape(str(value)).replace("−", "-").replace("≤", "<=").replace(" ", " ")
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _png_image(png: bytes, width: float) -> Image:
    image = Image(io.BytesIO(png))
    ratio = image.imageHeight / image.imageWidth
    image.drawWidth, image.drawHeight = width, width * ratio
    return image


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    ink, ink2, muted = (colors.HexColor(c) for c in (charts.INK, charts.INK_2, charts.MUTED))
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], alignment=TA_LEFT, fontSize=18, textColor=ink, spaceAfter=2
        ),
        "meta": ParagraphStyle("m", parent=base["Normal"], fontSize=8.5, textColor=muted),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=12.5,
            textColor=ink,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "b", parent=base["Normal"], fontSize=9.5, leading=13, textColor=ink2
        ),
        "bullet": ParagraphStyle(
            "bl",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13,
            textColor=ink2,
            leftIndent=10,
            bulletIndent=0,
        ),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=8, leading=10, textColor=ink2),
        "note": ParagraphStyle(
            "n",
            parent=base["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=ink2,
            backColor=colors.HexColor("#f4f3ef"),
            borderPadding=6,
            spaceBefore=6,
            spaceAfter=6,
        ),
        "kpi": ParagraphStyle(
            "kv",
            parent=base["Normal"],
            fontSize=16,
            leading=20,
            textColor=ink,
            fontName="Helvetica-Bold",
        ),
    }


def render_pdf(data: ReportData) -> bytes:
    """Rapport PDF A4 (graphiques matplotlib) dans la langue de ``data``."""
    lang = data.lang
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title=data.title,
        author="PriceWatch",
        subject=tr(lang, "pdf_subject"),
        lang=lang,
    )
    width = A4[0] - 3.6 * cm
    styles = _pdf_styles()
    money = partial(fmt_money, lang=lang)
    pct = partial(fmt_pct, lang=lang)

    def p(text: Any, style: str = "body") -> Paragraph:
        return Paragraph(_pdf_text(text), styles[style])

    generated = fmt_date(data.generated_at, with_time=True, lang=lang)
    story: list[Any] = [
        p(data.title, "title"),
        p(f"{tr(lang, 'generated_on', date=generated)} · PriceWatch v{__version__}", "meta"),
    ]
    if data.simulated:
        story.append(p(f"{tr(lang, 'demo_title')} {tr(lang, 'demo_body')}", "note"))
    s = data.summary
    kpis = [
        (tr(lang, "kpi_products_short"), fmt_number(s["n_products"], 0, lang=lang)),
        (tr(lang, "kpi_drops_short"), fmt_number(s["n_significant_drops"], 0, lang=lang)),
        (tr(lang, "kpi_anomalies_short"), fmt_number(s["n_anomalies"], 0, lang=lang)),
        (tr(lang, "kpi_avg_gap_short"), pct(s["avg_change_pct"])),
    ]
    kpi_table = Table(
        [
            [Paragraph(_pdf_text(v), styles["kpi"]) for _, v in kpis],
            [p(k, "meta") for k, _ in kpis],
        ],
        colWidths=[width / 4] * 4,
    )
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f3ef")),
                ("LINEAFTER", (0, 0), (-2, -1), 2, colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story += [Spacer(1, 8), kpi_table, p(tr(lang, "section_summary"), "h2")]
    story += [
        Paragraph(_pdf_text(point), styles["bullet"], bulletText="•") for point in data.highlights
    ]

    if not data.category_series.empty:
        story += [
            Spacer(1, 8),
            _png_image(charts.mpl_category_index(data.category_series, lang), width),
        ]
    if not data.drops.empty:
        story += [
            KeepTogether(
                [
                    p(tr(lang, "section_drops"), "h2"),
                    _png_image(charts.mpl_top_drops(data.drops, lang), width),
                ]
            ),
            Spacer(1, 6),
        ]
        header = ["col_product", "col_category", "col_price", "col_average", "col_gap", "col_trend"]
        rows: list[list[Any]] = [[tr(lang, key) for key in header]]
        rows += [
            [
                p(r.title, "cell"),
                p(r.category or "—", "cell"),
                money(r.last_price, r.currency),
                money(r.reference_price, r.currency),
                pct(r.drop_pct),
                trend_label(lang, str(r.trend)),
            ]
            for r in data.drops.itertuples()
        ]
        story.append(
            _table(
                rows, [6.2 * cm, 2.8 * cm, 2 * cm, 2 * cm, 1.6 * cm, 1.8 * cm], right_cols=(2, 3, 4)
            )
        )
        story += [
            Spacer(1, 10),
            KeepTogether(
                _png_image(
                    charts.mpl_product_history(data.history, data.drops.head(3), lang), width
                )
            ),
        ]
    if not data.stats.empty and data.stats["drop_pct"].notna().any():
        story += [
            Spacer(1, 8),
            _png_image(
                charts.mpl_change_distribution(data.stats, data.significant_drop_pct, lang), width
            ),
        ]

    story.append(p(tr(lang, "section_alerts"), "h2"))
    if data.alerts.empty:
        story.append(p(tr(lang, "no_alerts")))
    else:
        rows = [[tr(lang, "col_type"), tr(lang, "col_message"), tr(lang, "col_date")]]
        rows += [
            [kind_label(lang, a.kind), p(a.message, "cell"), fmt_date(a.created_at, lang=lang)]
            for a in data.alerts.itertuples()
        ]
        story.append(_table(rows, [3.4 * cm, 10.6 * cm, 2.4 * cm]))

    story += [p(tr(lang, "section_method"), "h2"), p(tr(lang, "method"))]
    muted = colors.HexColor(charts.MUTED)
    footer_text = _pdf_text(f"{data.title} — {fmt_date(data.generated_at, lang=lang)}")

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(1.8 * cm, 1 * cm, footer_text)
        canvas.drawRightString(A4[0] - 1.8 * cm, 1 * cm, f"{tr(lang, 'page')} {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _table(rows: list[list[Any]], widths: list[float], right_cols: tuple[int, ...] = ()) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(charts.INK_2)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(charts.AXIS)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(charts.GRID)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                *[("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols],
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


# -- orchestration ---------------------------------------------------------------
def generate_reports(
    repo: PriceRepository,
    config: AppConfig,
    output_dir: Path | None = None,
    formats: list[str] | None = None,
    generated_at: datetime | None = None,
    languages: list[str] | None = None,
) -> dict[str, Path]:
    """Génère les rapports datés pour chaque format et chaque langue.

    Fichiers produits dans ``output_dir`` :

    - ``pricewatch_report_<date>_<langue>.<format>`` (rapports datés) ;
    - ``latest_<langue>.<format>`` (dernier rapport par langue) ;
    - ``latest.<format>`` (dernier rapport dans la langue par défaut, la première).

    Returns:
        Chemins des rapports datés, clés ``"<format>_<langue>"`` (ex. ``"pdf_en"``).
    """
    output_dir = Path(output_dir or config.report.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fmts = formats or list(config.report.formats)
    for fmt in fmts:
        if fmt not in ("html", "pdf"):
            raise ValueError(f"Format de rapport inconnu : {fmt}")
    langs: list[str] = [check_lang(lang) for lang in (languages or config.report.languages)]
    generated_at = generated_at or datetime.now(UTC)
    stamp = generated_at.strftime("%Y-%m-%d")
    paths: dict[str, Path] = {}
    for lang in langs:
        data = build_report_data(repo, config, generated_at, lang)
        for fmt in fmts:
            path = output_dir / report_filename(stamp, lang, fmt)
            if fmt == "html":
                alternates = {
                    other: report_filename(stamp, other, "html") for other in langs if other != lang
                }
                path.write_text(render_html(data, alternates), encoding="utf-8")
            else:
                path.write_bytes(render_pdf(data))
            shutil.copyfile(path, output_dir / f"latest_{lang}.{fmt}")
            if lang == langs[0]:
                shutil.copyfile(path, output_dir / f"latest.{fmt}")
            paths[f"{fmt}_{lang}"] = path
            logger.info("Rapport %s (%s) généré : %s", fmt.upper(), lang, path)
    return paths
