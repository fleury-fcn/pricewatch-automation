"""Génération du rapport daté (HTML interactif via Jinja2, PDF via ReportLab)."""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from pricewatch.alerts import KIND_LABELS
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
from pricewatch.logging_setup import get_logger
from pricewatch.reporting import charts
from pricewatch.storage.repository import PriceRepository

logger = get_logger(__name__)


@dataclass
class ReportData:
    """Tout ce qu'il faut pour produire un rapport (indépendant du format)."""

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
    highlights: list[str] = field(default_factory=list)

    @property
    def currency(self) -> str:
        """Devise majoritaire des produits suivis."""
        if self.stats.empty:
            return "EUR"
        return str(self.stats["currency"].mode().iloc[0])


def executive_summary(data: ReportData) -> list[str]:
    """Rédige les points clés du résumé exécutif à partir des indicateurs."""
    s = data.summary
    if not s["n_products"]:
        return ["Aucune donnée de prix disponible : lancez d'abord une collecte."]
    points = [
        f"{s['n_products']} produits suivis, {fmt_number(s['n_observations'], 0)} observations "
        f"du {fmt_date(s['period_start'])} au {fmt_date(s['period_end'])}.",
        f"Écart moyen du dernier prix à la moyenne récente : {fmt_pct(s['avg_change_pct'])} ; "
        f"{s['n_drops']} produits en baisse et {s['n_rises']} en hausse depuis la veille.",
    ]
    if biggest := s.get("biggest_drop"):
        points.append(
            f"Plus forte baisse : « {biggest['title']} » à "
            f"{fmt_money(biggest['last_price'], biggest['currency'])} "
            f"({fmt_pct(biggest['drop_pct'])} vs {fmt_money(biggest['reference_price'], biggest['currency'])})."
        )
    points.append(
        f"{s['n_significant_drops']} baisse(s) significative(s) (≤ −{data.significant_drop_pct:.0f} %), "
        f"{s['n_anomalies']} prix anormal(aux) (z-score), "
        f"{s['n_targets_reached']} prix cible(s) atteint(s), "
        f"{s['n_all_time_lows']} plus bas historique(s)."
    )
    if not data.alerts.empty:
        points.append(f"{len(data.alerts)} alerte(s) émise(s) lors de la dernière exécution.")
    return points


def build_report_data(
    repo: PriceRepository, config: AppConfig, generated_at: datetime | None = None
) -> ReportData:
    """Assemble les données du rapport depuis la base."""
    history = repo.get_price_history()
    analysis = config.analysis
    stats = compute_product_stats(history, analysis, config.target_prices())
    flagged = flag_anomalies(history, analysis.zscore_threshold, analysis.trend_window_days)
    runs = repo.get_runs(limit=10)
    alerts = repo.get_alerts(limit=200)
    if not runs.empty and not alerts.empty:
        alerts = alerts[alerts["run_id"] == runs.iloc[0]["id"]]
    data = ReportData(
        title=config.report.title,
        generated_at=generated_at or datetime.now(UTC),
        summary=summarize(stats, history),
        stats=stats,
        history=flagged,
        drops=top_drops(stats, config.report.top_n),
        rises=top_rises(stats, config.report.top_n),
        alerts=alerts.reset_index(drop=True),
        runs=runs,
        category_series=charts.fold_categories(category_index(history), history),
        significant_drop_pct=analysis.significant_drop_pct,
        simulated=any(s.simulate_price_moves for s in config.enabled_sources),
    )
    data.highlights = executive_summary(data)
    return data


# -- HTML ------------------------------------------------------------------------
def _jinja() -> Environment:
    env = Environment(
        loader=PackageLoader("pricewatch", "reporting/templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(
        money=fmt_money,
        pct=fmt_pct,
        num=fmt_number,
        date=fmt_date,
        kind_label=lambda k: KIND_LABELS.get(k, k),
    )
    return env


def render_html(data: ReportData) -> str:
    """Rapport HTML autonome (graphiques Plotly interactifs, plotly.js via CDN)."""
    figures: list[tuple[str, Any]] = []
    if not data.category_series.empty:
        figures.append(("category", charts.px_category_index(data.category_series)))
    if not data.drops.empty:
        figures.append(("drops", charts.px_top_drops(data.drops)))
        figures.append(("history", charts.px_product_history(data.history, data.drops.head(3))))
    if not data.stats.empty and data.stats["drop_pct"].notna().any():
        figures.append(
            ("distribution", charts.px_change_distribution(data.stats, data.significant_drop_pct))
        )
    html_figures = {
        name: fig.to_html(
            full_html=False,
            include_plotlyjs="cdn" if i == 0 else False,
            config={"displaylogo": False, "responsive": True},
            div_id=f"chart-{name}",
        )
        for i, (name, fig) in enumerate(figures)
    }
    products = data.stats.sort_values("drop_pct", na_position="last")
    return (
        _jinja()
        .get_template("report.html.j2")
        .render(
            data=data,
            s=data.summary,
            figures=html_figures,
            products=products,
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


def render_pdf(data: ReportData) -> bytes:
    """Rapport PDF A4 (graphiques matplotlib)."""
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
        subject="Rapport de suivi des prix",
    )
    width = A4[0] - 3.6 * cm
    base = getSampleStyleSheet()
    ink, ink2, muted = (colors.HexColor(c) for c in (charts.INK, charts.INK_2, charts.MUTED))
    styles = {
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
    }
    p = lambda text, style="body": Paragraph(_pdf_text(text), styles[style])  # noqa: E731

    story: list[Any] = [
        p(data.title, "title"),
        p(
            f"Généré le {fmt_date(data.generated_at, with_time=True)} · PriceWatch v{__version__}",
            "meta",
        ),
    ]
    if data.simulated:
        story.append(
            p(
                "Mode démo : la source books.toscrape.com a des prix fixes ; les variations "
                "présentées sont simulées de façon déterministe à partir des prix réels collectés.",
                "note",
            )
        )
    s = data.summary
    kpis = [
        ("Produits suivis", fmt_number(s["n_products"], 0)),
        ("Baisses significatives", fmt_number(s["n_significant_drops"], 0)),
        ("Anomalies", fmt_number(s["n_anomalies"], 0)),
        ("Écart moyen", fmt_pct(s["avg_change_pct"])),
    ]
    kpi_value = ParagraphStyle(
        "kv",
        parent=styles["body"],
        fontSize=16,
        leading=20,
        textColor=ink,
        fontName="Helvetica-Bold",
    )
    kpi_table = Table(
        [[Paragraph(_pdf_text(v), kpi_value) for _, v in kpis], [p(k, "meta") for k, _ in kpis]],
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
    story += [Spacer(1, 8), kpi_table, p("Résumé exécutif", "h2")]
    story += [
        Paragraph(_pdf_text(point), styles["bullet"], bulletText="•") for point in data.highlights
    ]

    if not data.category_series.empty:
        story += [Spacer(1, 8), _png_image(charts.mpl_category_index(data.category_series), width)]
    if not data.drops.empty:
        story += [
            KeepTogether(
                [p("Meilleures baisses", "h2"), _png_image(charts.mpl_top_drops(data.drops), width)]
            ),
            Spacer(1, 6),
        ]
        rows = [["Produit", "Catégorie", "Prix", "Moyenne", "Écart", "Tendance"]]
        rows += [
            [
                p(r.title, "cell"),
                p(r.category or "—", "cell"),
                fmt_money(r.last_price, r.currency),
                fmt_money(r.reference_price, r.currency),
                fmt_pct(r.drop_pct),
                r.trend,
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
                _png_image(charts.mpl_product_history(data.history, data.drops.head(3)), width)
            ),
        ]
    if not data.stats.empty and data.stats["drop_pct"].notna().any():
        story += [
            Spacer(1, 8),
            _png_image(
                charts.mpl_change_distribution(data.stats, data.significant_drop_pct), width
            ),
        ]

    story.append(p("Alertes de la dernière exécution", "h2"))
    if data.alerts.empty:
        story.append(p("Aucune alerte émise."))
    else:
        rows = [["Type", "Message", "Date"]]
        rows += [
            [KIND_LABELS.get(a.kind, a.kind), p(a.message, "cell"), fmt_date(a.created_at)]
            for a in data.alerts.itertuples()
        ]
        story.append(_table(rows, [3.2 * cm, 11 * cm, 2.2 * cm]))

    story += [
        p("Méthodologie", "h2"),
        p(
            "Collecte HTTP respectueuse (robots.txt, délai entre requêtes, User-Agent identifié, "
            "nouvelles tentatives avec backoff). Prix nettoyés et validés (pydantic), stockés dans "
            "SQLite. Écart = dernier prix vs moyenne des observations de la fenêtre de tendance ; "
            "anomalie = valeur absolue du z-score du dernier prix (vs historique antérieur) "
            "au-dessus du seuil ; "
            "tendance = pente d'une régression linéaire sur la fenêtre."
        ),
    ]

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(
            1.8 * cm, 1 * cm, _pdf_text(f"{data.title} — {fmt_date(data.generated_at)}")
        )
        canvas.drawRightString(A4[0] - 1.8 * cm, 1 * cm, f"Page {document.page}")
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
) -> dict[str, Path]:
    """Génère les rapports datés et met à jour ``latest.<ext>``.

    Returns:
        Chemins des rapports datés, par format.
    """
    output_dir = Path(output_dir or config.report.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_report_data(repo, config, generated_at)
    stamp = data.generated_at.strftime("%Y-%m-%d")
    paths: dict[str, Path] = {}
    for fmt in formats or config.report.formats:
        path = output_dir / f"pricewatch_report_{stamp}.{fmt}"
        if fmt == "html":
            path.write_text(render_html(data), encoding="utf-8")
        elif fmt == "pdf":
            path.write_bytes(render_pdf(data))
        else:
            raise ValueError(f"Format de rapport inconnu : {fmt}")
        shutil.copyfile(path, output_dir / f"latest.{fmt}")
        paths[fmt] = path
        logger.info("Rapport %s généré : %s", fmt.upper(), path)
    return paths
