"""Graphiques du rapport : Plotly (interactif, HTML/dashboard) et matplotlib (PNG pour le PDF).

Les deux familles partagent la même palette et les mêmes données préparées, pour que le
PDF et la version interactive racontent exactement la même chose.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # rendu sans affichage (CI, serveurs)

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

# Palette catégorielle validée (ordre fixe, jamais recyclé) et encres neutres.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
OTHER = "#898781"
CRITICAL = "#d03b3b"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
FONT = "system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif"
OTHER_LABEL = "Autres"


# -- préparation des données --------------------------------------------------------
def fold_categories(
    index: pd.DataFrame, history: pd.DataFrame, max_series: int = 4
) -> pd.DataFrame:
    """Garde les ``max_series`` catégories les plus fournies, regroupe le reste dans « Autres ».

    Args:
        index: Sortie de :func:`pricewatch.analysis.category_index`.
        history: Historique (pour compter les produits par catégorie).
        max_series: Nombre de catégories affichées individuellement.
    """
    if index.empty:
        return index
    counts = (
        history.assign(category=history["category"].fillna("Non classé"))
        .groupby("category")["product_id"]
        .nunique()
        .sort_values(ascending=False, kind="stable")
    )
    keep = list(counts.index[:max_series])
    folded = index.assign(
        category=index["category"].where(index["category"].isin(keep), OTHER_LABEL)
    )
    out = folded.groupby(["date", "category"], as_index=False)["index"].mean()
    order = keep + ([OTHER_LABEL] if OTHER_LABEL in set(out["category"]) else [])
    out["category"] = pd.Categorical(out["category"], categories=order, ordered=True)
    return out.sort_values(["category", "date"]).reset_index(drop=True)


def category_colors(categories: list[str]) -> dict[str, str]:
    """Couleur stable par catégorie (l'ordre d'entrée fixe l'attribution)."""
    colors: dict[str, str] = {}
    slot = 0
    for cat in categories:
        if cat == OTHER_LABEL:
            colors[cat] = OTHER
        else:
            colors[cat] = SERIES[slot % len(SERIES)]
            slot += 1
    return colors


def _short(text: str, width: int = 38) -> str:
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


# -- matplotlib (PDF) ------------------------------------------------------------
def _mpl_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 9,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "legend.frameon": False,
            "legend.labelcolor": INK_2,
        }
    )


def _date_axis(ax: plt.Axes, max_ticks: int = 8) -> None:
    """Dates courtes (jj/mm), horizontales, en nombre limité."""
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=max_ticks))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))


def _png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return buffer.getvalue()


def mpl_category_index(folded: pd.DataFrame) -> bytes:
    """Indice de prix par catégorie (base 100) — PNG."""
    _mpl_style()
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    cats = [str(c) for c in folded["category"].cat.categories] if not folded.empty else []
    colors = category_colors(cats)
    ends: list[tuple[float, pd.Timestamp]] = []
    for cat in cats:
        part = folded[folded["category"] == cat]
        if part.empty:
            continue
        ax.plot(part["date"], part["index"], color=colors[cat], linewidth=1.6, label=cat)
        ends.append((float(part["index"].iloc[-1]), part["date"].iloc[-1]))
    for value, y_text, x in _spread_labels(ends, ax):
        ax.annotate(
            f"{value:.0f}",
            (x, value),
            xytext=(x, y_text),
            va="center",
            ha="left",
            fontsize=8,
            color=INK_2,
            annotation_clip=False,
        )
    ax.axhline(100, color=AXIS, linewidth=0.8)
    ax.set_title("Indice de prix par catégorie (base 100, médiane des produits)", pad=24)
    ax.legend(
        loc="lower left",
        ncol=max(len(cats), 1),
        fontsize=8,
        bbox_to_anchor=(0, 1.0),
        handlelength=1.4,
        columnspacing=1.2,
        borderaxespad=0.2,
    )
    _date_axis(ax)
    return _png(fig)


def _spread_labels(
    ends: list[tuple[float, pd.Timestamp]], ax: plt.Axes, min_gap_ratio: float = 0.045
) -> list[tuple[float, float, pd.Timestamp]]:
    """Écarte verticalement les étiquettes de fin de ligne pour éviter les chevauchements.

    Returns:
        ``(valeur, ordonnée_du_texte, abscisse_du_texte)`` pour chaque série.
    """
    if not ends:
        return []
    low, high = ax.get_ylim()
    gap = (high - low) * min_gap_ratio
    placed: list[tuple[float, float, pd.Timestamp]] = []
    for value, x in sorted(ends, key=lambda e: e[0]):
        y = value if not placed else max(value, placed[-1][1] + gap)
        placed.append((value, y, x + pd.Timedelta(hours=18)))
    return placed


def mpl_top_drops(drops: pd.DataFrame) -> bytes:
    """Plus fortes baisses vs moyenne récente — barres horizontales, PNG."""
    _mpl_style()
    data = drops.iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.2, max(1.6, 0.32 * len(data) + 0.8)))
    labels = [_short(str(t)) for t in data["title"]]
    ax.barh(labels, data["drop_pct"], color=SERIES[0], height=0.6)
    for y, value in enumerate(data["drop_pct"]):
        ax.annotate(
            f"{value:.1f} %",
            (value, y),
            xytext=(-4, 0),
            textcoords="offset points",
            ha="right",
            va="center",
            fontsize=8,
            color=INK_2,
        )
    ax.grid(axis="y", visible=False)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    lo = float(data["drop_pct"].min()) if not data.empty else -1.0
    ax.set_xlim(lo * 1.25, 0)
    ax.set_title("Meilleures baisses vs moyenne récente (%)")
    ax.tick_params(axis="y", colors=INK_2)
    return _png(fig)


def mpl_product_history(history: pd.DataFrame, stats: pd.DataFrame) -> bytes:
    """Petits multiples : historique des produits de ``stats`` avec anomalies — PNG."""
    _mpl_style()
    n = max(len(stats), 1)
    fig, axes = plt.subplots(1, n, figsize=(7.2, 2.4), squeeze=False)
    for ax, (_, row) in zip(axes[0], stats.iterrows(), strict=False):
        part = history[history["product_id"] == row["product_id"]]
        ax.plot(part["collected_at"], part["price"], color=SERIES[0], linewidth=1.4)
        if "is_anomaly" in part:
            spikes = part[part["is_anomaly"]]
            ax.scatter(
                spikes["collected_at"],
                spikes["price"],
                s=22,
                color=CRITICAL,
                edgecolors=SURFACE,
                linewidths=1.2,
                zorder=3,
                label="anomalie",
            )
        if pd.notna(row.get("reference_price")):
            ax.axhline(row["reference_price"], color=MUTED, linewidth=0.8)
        ax.set_title(_short(str(row["title"]), 26), fontsize=8.5)
        _date_axis(ax, max_ticks=4)
        ax.tick_params(axis="x", labelsize=7)
        ax.tick_params(axis="y", labelsize=7)
    for ax in axes[0][len(stats) :]:
        ax.set_visible(False)
    handles, labels = axes[0][0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower right", fontsize=7)
    fig.suptitle(
        "Historique des plus fortes baisses (trait gris : moyenne de référence)",
        x=0.01,
        ha="left",
        fontsize=10,
        fontweight="bold",
        color=INK,
    )
    fig.tight_layout()
    return _png(fig)


def mpl_change_distribution(stats: pd.DataFrame, threshold_pct: float) -> bytes:
    """Distribution des écarts au prix de référence — histogramme, PNG."""
    _mpl_style()
    values = stats["drop_pct"].dropna()
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    ax.hist(values, bins=25, color=SERIES[0], edgecolor=SURFACE, linewidth=1.5)
    ax.axvline(-threshold_pct, color=INK_2, linewidth=1)
    ax.annotate(
        f"seuil −{threshold_pct:.0f} %",
        (-threshold_pct, ax.get_ylim()[1] * 0.92),
        xytext=(4, 0),
        textcoords="offset points",
        fontsize=8,
        color=INK_2,
    )
    ax.set_title("Écart du dernier prix à la moyenne récente (%)")
    ax.set_ylabel("produits")
    return _png(fig)


# -- Plotly (HTML / dashboard) ------------------------------------------------------
def _layout(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "font": {"size": 15, "color": INK}},
        height=height,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font={"family": FONT, "color": INK_2, "size": 12},
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
        legend={"orientation": "h", "y": -0.15, "x": 0},
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, tickfont={"color": MUTED}, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, tickfont={"color": MUTED}, zeroline=False)
    return fig


def px_category_index(folded: pd.DataFrame) -> go.Figure:
    """Indice de prix par catégorie — interactif."""
    fig = go.Figure()
    cats = [str(c) for c in folded["category"].cat.categories] if not folded.empty else []
    colors = category_colors(cats)
    for cat in cats:
        part = folded[folded["category"] == cat]
        if part.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=part["date"],
                y=part["index"],
                name=cat,
                mode="lines",
                line={"color": colors[cat], "width": 2},
                hovertemplate="%{x|%d/%m/%Y}<br>" + cat + " : %{y:.1f}<extra></extra>",
            )
        )
    fig.add_hline(y=100, line={"color": AXIS, "width": 1})
    fig.update_layout(hovermode="x unified")
    return _layout(fig, "Indice de prix par catégorie (base 100, médiane des produits)")


def px_top_drops(drops: pd.DataFrame) -> go.Figure:
    """Meilleures baisses — barres horizontales interactives."""
    data = drops.iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=data["drop_pct"],
            y=[_short(str(t), 45) for t in data["title"]],
            orientation="h",
            marker={"color": SERIES[0], "cornerradius": 4},
            width=0.6,
            text=[f"{v:.1f} %" for v in data["drop_pct"]],
            textposition="outside",
            textfont={"color": INK_2},
            customdata=data[["last_price", "reference_price", "currency"]].to_numpy(),
            hovertemplate=(
                "%{y}<br>Écart : %{x:.1f} %<br>Prix : %{customdata[0]:.2f} %{customdata[2]}"
                "<br>Moyenne : %{customdata[1]:.2f} %{customdata[2]}<extra></extra>"
            ),
        )
    )
    low = float(data["drop_pct"].min()) if not data.empty else -1.0
    fig.update_xaxes(ticksuffix=" %", range=[low * 1.3, 0])  # marge pour les étiquettes
    return _layout(
        fig, "Meilleures baisses vs moyenne récente", height=max(260, 34 * len(data) + 90)
    )


def px_product_history(history: pd.DataFrame, stats: pd.DataFrame) -> go.Figure:
    """Historique de prix de plusieurs produits (petits multiples, anomalies marquées)."""
    n = max(len(stats), 1)
    titles = [_short(str(t), 30) for t in stats["title"]] or [""]
    fig = make_subplots(rows=1, cols=n, subplot_titles=titles, horizontal_spacing=0.06)
    for i, (_, row) in enumerate(stats.iterrows(), start=1):
        part = history[history["product_id"] == row["product_id"]]
        fig.add_trace(
            go.Scatter(
                x=part["collected_at"],
                y=part["price"],
                mode="lines",
                name="prix",
                line={"color": SERIES[0], "width": 2},
                showlegend=i == 1,
                hovertemplate="%{x|%d/%m/%Y}<br>%{y:.2f} "
                + str(row["currency"])
                + "<extra></extra>",
            ),
            row=1,
            col=i,
        )
        if "is_anomaly" in part:
            spikes = part[part["is_anomaly"]]
            fig.add_trace(
                go.Scatter(
                    x=spikes["collected_at"],
                    y=spikes["price"],
                    mode="markers",
                    name="anomalie",
                    marker={"color": CRITICAL, "size": 9, "line": {"color": SURFACE, "width": 2}},
                    showlegend=i == 1,
                    hovertemplate="Anomalie<br>%{x|%d/%m/%Y}<br>%{y:.2f}<extra></extra>",
                ),
                row=1,
                col=i,
            )
        if pd.notna(row.get("reference_price")):
            fig.add_hline(y=row["reference_price"], line={"color": MUTED, "width": 1}, row=1, col=i)
    fig.update_annotations(font={"size": 12, "color": INK_2})
    return _layout(fig, "Historique des plus fortes baisses (gris : moyenne de référence)", 340)


def px_price_history(history: pd.DataFrame, product_ids: list[int]) -> go.Figure:
    """Historique de prix superposé pour une sélection de produits (dashboard)."""
    fig = go.Figure()
    subset = history[history["product_id"].isin(product_ids)]
    titles = subset.drop_duplicates("product_id").set_index("product_id")["title"]
    for slot, pid in enumerate(product_ids[: len(SERIES)]):
        part = subset[subset["product_id"] == pid]
        if part.empty:
            continue
        name = _short(str(titles.get(pid, pid)), 40)
        fig.add_trace(
            go.Scatter(
                x=part["collected_at"],
                y=part["price"],
                name=name,
                mode="lines",
                line={"color": SERIES[slot], "width": 2},
                hovertemplate="%{x|%d/%m/%Y}<br>" + name + " : %{y:.2f}<extra></extra>",
            )
        )
    fig.update_layout(hovermode="x unified")
    return _layout(fig, "Historique des prix")


def px_change_distribution(stats: pd.DataFrame, threshold_pct: float) -> go.Figure:
    """Distribution des écarts au prix de référence — histogramme interactif."""
    fig = go.Figure(
        go.Histogram(
            x=stats["drop_pct"].dropna(),
            nbinsx=25,
            marker={"color": SERIES[0], "line": {"color": SURFACE, "width": 2}},
            hovertemplate="%{x} %<br>%{y} produits<extra></extra>",
        )
    )
    fig.add_vline(
        x=-threshold_pct,
        line={"color": INK_2, "width": 1},
        annotation_text=f"seuil −{threshold_pct:.0f} %",
        annotation_font_color=INK_2,
    )
    fig.update_xaxes(ticksuffix=" %")
    fig.update_yaxes(title_text="produits")
    return _layout(fig, "Écart du dernier prix à la moyenne récente", 300)
