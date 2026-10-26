"""Dashboard Streamlit de PriceWatch.

Deux modes :

- **démo** (par défaut si aucune base locale, et sur Streamlit Community Cloud) : une base
  SQLite temporaire est construite au démarrage à partir du jeu de données versionné
  ``data/demo/price_history.csv`` — aucune dépendance à un stockage persistant ;
- **local** : lit ``data/pricewatch.db`` (alimentée par ``pricewatch run``).

Forcer un mode : variable d'environnement ``PRICEWATCH_MODE=demo|local``.
Lancement : ``streamlit run app/streamlit_app.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # fonctionne même sans `pip install -e .`
    sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from pricewatch import __version__, demo  # noqa: E402
from pricewatch.alerts import KIND_LABELS, build_alerts  # noqa: E402
from pricewatch.analysis import (  # noqa: E402
    category_index,
    compute_product_stats,
    flag_anomalies,
    summarize,
    top_drops,
)
from pricewatch.config import AppConfig, load_config  # noqa: E402
from pricewatch.pipeline import run_pipeline  # noqa: E402
from pricewatch.reporting import charts, generate_reports  # noqa: E402
from pricewatch.reporting.report import ReportData, executive_summary  # noqa: E402
from pricewatch.storage import PriceRepository  # noqa: E402

CONFIG_PATH = ROOT / "config" / "config.yaml"
LOCAL_DB = ROOT / "data" / "pricewatch.db"
DEMO_HISTORY = ROOT / "data" / "demo" / demo.HISTORY_FILE
DEMO_CATALOGUE_DIR = ROOT / "data" / "demo"
REPO_URL = "https://github.com/fleury-fcn/pricewatch-automation"

st.set_page_config(page_title="PriceWatch", page_icon="📉", layout="wide")


def resolve_mode() -> str:
    """Mode d'exécution : variable d'environnement, sinon présence d'une base locale."""
    forced = os.environ.get("PRICEWATCH_MODE", "").lower()
    if forced in {"demo", "local"}:
        return forced
    if os.environ.get("PRICEWATCH_DATABASE_URL"):
        return "local"
    return "local" if LOCAL_DB.is_file() and LOCAL_DB.stat().st_size > 0 else "demo"


@st.cache_resource(show_spinner="Préparation des données…")
def get_context(mode: str) -> tuple[AppConfig, PriceRepository]:
    """Configuration et dépôt de la session (partagés entre les reruns)."""
    config = load_config(CONFIG_PATH)
    config.logging.file = None
    if mode == "demo":
        workdir = Path(tempfile.mkdtemp(prefix="pricewatch-"))
        config.storage.database_url = f"sqlite:///{workdir / 'demo.db'}"
        config.report.output_dir = workdir / "reports"
        repo = PriceRepository.from_url(config.storage.database_url)
        demo.load_history(repo, DEMO_HISTORY)
        _seed_demo_alerts(repo, config)
    else:
        # PRICEWATCH_DATABASE_URL (Docker, serveur) prime sur la base locale du dépôt
        if not os.environ.get("PRICEWATCH_DATABASE_URL"):
            config.storage.database_url = f"sqlite:///{LOCAL_DB}"
        config.report.output_dir = ROOT / "reports"
        repo = PriceRepository.from_url(config.storage.database_url)
        repo.init_db()
    return config, repo


def _seed_demo_alerts(repo: PriceRepository, config: AppConfig) -> None:
    """Calcule les alertes du dernier jour du jeu de démo (onglet Alertes non vide)."""
    run_id = repo.start_run(mode="demo")
    stats = compute_product_stats(repo.get_price_history(), config.analysis, config.target_prices())
    alerts = build_alerts(stats, config.alerts)
    repo.save_alerts(alerts, run_id)
    repo.finish_run(
        run_id, "success", n_valid=len(stats), n_alerts=len(alerts), error="Jeu de données de démo"
    )


@st.cache_data(show_spinner=False)
def load_frames(_repo: PriceRepository, _config: AppConfig, version: tuple[int, int]) -> dict:
    """Données de base (recalculées seulement si la base change : ``version``)."""
    history = _repo.get_price_history()
    return {
        "history": history,
        "alerts": _repo.get_alerts(limit=500),
        "runs": _repo.get_runs(limit=50),
    }


def data_version(repo: PriceRepository) -> tuple[int, int]:
    """Empreinte de la base (observations, dernière exécution) servant de clé de cache."""
    runs = repo.get_runs(limit=1)
    return repo.count_observations(), int(runs.iloc[0]["id"]) if not runs.empty else 0


def money_column(label: str) -> Any:
    """Colonne monétaire à deux décimales."""
    return st.column_config.NumberColumn(label, format="%.2f")


# -- contexte ----------------------------------------------------------------------
mode = resolve_mode()
config, repo = get_context(mode)
frames = load_frames(repo, config, data_version(repo))
history_all: pd.DataFrame = frames["history"]

# -- barre latérale ------------------------------------------------------------------
with st.sidebar:
    st.title("📉 PriceWatch")
    st.caption(f"v{__version__} · [code source]({REPO_URL})")
    if mode == "demo":
        st.info(
            "**Mode démo** : jeu de données embarqué (60 livres réels de books.toscrape.com, "
            "variations de prix simulées de façon déterministe). Le stockage est éphémère."
        )
    else:
        st.success(f"**Mode local** : `{config.storage.database_url}`")

    categories = sorted(history_all["category"].dropna().unique()) if not history_all.empty else []
    selected_categories = st.multiselect("Catégories", categories, placeholder="Toutes")

    st.divider()
    st.subheader("Pipeline")
    online = st.toggle(
        "Collecte en ligne",
        value=True,
        help="Désactivé : rejoue le catalogue embarqué (aucune requête réseau).",
    )
    max_pages = st.slider(
        "Pages à collecter",
        1,
        3,
        1,
        disabled=not online,
        help="20 produits par page, 1 seconde entre deux requêtes.",
    )
    if st.button("▶️ Lancer le pipeline", type="primary", use_container_width=True):
        run_config = config.model_copy(deep=True)
        for source in run_config.sources:
            source.max_pages = max_pages
        products = (
            None if online else demo.catalogue_as_raw(demo.load_catalogue(DEMO_CATALOGUE_DIR))
        )
        with st.status("Exécution du pipeline…", expanded=True) as status:
            st.write("Collecte, nettoyage, stockage, analyse, alertes, rapport…")
            try:
                result = run_pipeline(
                    run_config, repo, products=products, send_alerts=mode == "local"
                )
            except Exception as exc:  # affiché à l'utilisateur, détaillé dans les logs
                status.update(label="Échec du pipeline", state="error")
                st.error(f"{type(exc).__name__} : {exc}")
            else:
                label = f"Exécution #{result.run_id} : {result.status} ({result.duration_s} s)"
                status.update(
                    label=label, state="complete" if result.status != "failed" else "error"
                )
                st.write(
                    f"{result.n_collected} collectés · {result.n_valid} valides · "
                    f"{result.n_stored} nouvelles observations · {len(result.alerts)} alertes"
                )
                if result.status == "failed" and online:
                    st.warning(
                        "Collecte impossible : réessayez avec « Collecte en ligne » désactivé."
                    )
        st.cache_data.clear()
        st.session_state["last_run"] = datetime.now(UTC)
        st.rerun()

# -- données filtrées ------------------------------------------------------------------
history = (
    history_all[history_all["category"].isin(selected_categories)]
    if selected_categories
    else history_all
)
stats = compute_product_stats(history, config.analysis, config.target_prices())
summary = summarize(stats, history)

st.title("Suivi des prix e-commerce")
if history.empty:
    st.warning("Aucune donnée : lancez le pipeline depuis la barre latérale.")
    st.stop()
st.caption(
    f"{summary['n_products']} produits · {summary['n_observations']:,} observations · "
    f"du {summary['period_start']:%d/%m/%Y} au {summary['period_end']:%d/%m/%Y}".replace(",", " ")
)

cols = st.columns(5)
cols[0].metric("Produits suivis", summary["n_products"])
cols[1].metric(
    "Baisses significatives",
    summary["n_significant_drops"],
    help=f"Dernier prix ≤ −{config.analysis.significant_drop_pct:.0f} % vs moyenne récente",
)
cols[2].metric(
    "Prix anormaux", summary["n_anomalies"], help=f"|z-score| ≥ {config.analysis.zscore_threshold}"
)
cols[3].metric("Prix cibles atteints", summary["n_targets_reached"])
cols[4].metric(
    "Écart moyen",
    f"{summary['avg_change_pct']:+.1f} %",
    help="Dernier prix vs moyenne de la fenêtre de tendance",
)

tab_overview, tab_history, tab_alerts, tab_runs, tab_report = st.tabs(
    ["Vue d'ensemble", "Historique des prix", "Alertes", "Exécutions", "Rapport PDF"]
)
flagged = flag_anomalies(
    history, config.analysis.zscore_threshold, config.analysis.trend_window_days
)
drops = top_drops(stats, config.report.top_n)
PLOTLY = {"displaylogo": False}

with tab_overview:
    report_data = ReportData(
        title="",
        generated_at=datetime.now(UTC),
        summary=summary,
        stats=stats,
        history=flagged,
        drops=drops,
        rises=stats.iloc[0:0],
        alerts=frames["alerts"].iloc[0:0],
        runs=frames["runs"],
        category_series=pd.DataFrame(),
        significant_drop_pct=config.analysis.significant_drop_pct,
    )
    st.markdown("\n".join(f"- {point}" for point in executive_summary(report_data)))
    folded = charts.fold_categories(category_index(history), history)
    st.plotly_chart(
        charts.px_category_index(folded), use_container_width=True, theme=None, config=PLOTLY
    )
    if drops.empty:
        st.info("Aucune baisse par rapport à la moyenne récente.")
    else:
        st.plotly_chart(
            charts.px_top_drops(drops), use_container_width=True, theme=None, config=PLOTLY
        )
    st.plotly_chart(
        charts.px_change_distribution(stats, config.analysis.significant_drop_pct),
        use_container_width=True,
        theme=None,
        config=PLOTLY,
    )
    st.subheader("Meilleures baisses")
    st.dataframe(
        drops[["title", "category", "last_price", "reference_price", "drop_pct", "trend", "url"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "title": "Produit",
            "category": "Catégorie",
            "last_price": money_column("Prix (£)"),
            "reference_price": money_column("Moyenne récente (£)"),
            "drop_pct": st.column_config.NumberColumn("Écart", format="%.1f %%"),
            "trend": "Tendance",
            "url": st.column_config.LinkColumn("Lien", display_text="ouvrir"),
        },
    )

with tab_history:
    options = stats.sort_values("drop_pct", na_position="last")
    labels = dict(zip(options["product_id"], options["title"], strict=True))
    chosen = st.multiselect(
        "Produits (5 au maximum)",
        list(labels),
        default=list(drops["product_id"].head(3)),
        format_func=lambda pid: labels[pid],
        max_selections=5,
    )
    if chosen:
        st.plotly_chart(
            charts.px_price_history(history, chosen),
            use_container_width=True,
            theme=None,
            config=PLOTLY,
        )
        st.plotly_chart(
            charts.px_product_history(flagged, stats[stats["product_id"].isin(chosen)]),
            use_container_width=True,
            theme=None,
            config=PLOTLY,
        )
        detail = stats[stats["product_id"].isin(chosen)]
        st.dataframe(
            detail[
                [
                    "title",
                    "last_price",
                    "min_price",
                    "max_price",
                    "drop_pct",
                    "trend",
                    "trend_pct_per_day",
                    "zscore",
                    "n_obs",
                ]
            ],
            hide_index=True,
            use_container_width=True,
            column_config={
                "title": "Produit",
                "last_price": money_column("Dernier prix"),
                "min_price": money_column("Min"),
                "max_price": money_column("Max"),
                "drop_pct": st.column_config.NumberColumn("Écart", format="%.1f %%"),
                "trend": "Tendance",
                "trend_pct_per_day": st.column_config.NumberColumn("Pente (%/j)", format="%.2f"),
                "zscore": st.column_config.NumberColumn("z-score", format="%.2f"),
                "n_obs": "Observations",
            },
        )
        with st.expander("Données brutes"):
            raw = history[history["product_id"].isin(chosen)].sort_values("collected_at")
            st.dataframe(
                raw[["collected_at", "title", "price", "currency", "in_stock"]],
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.info("Sélectionnez au moins un produit.")

with tab_alerts:
    alerts = frames["alerts"]
    if alerts.empty:
        st.info("Aucune alerte enregistrée.")
    else:
        alerts = alerts.assign(type=alerts["kind"].map(KIND_LABELS).fillna(alerts["kind"]))
        st.dataframe(
            alerts[["created_at", "type", "message", "price", "change_pct", "run_id"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "created_at": st.column_config.DatetimeColumn("Date", format="DD/MM/YYYY HH:mm"),
                "type": "Type",
                "message": "Message",
                "price": money_column("Prix"),
                "change_pct": st.column_config.NumberColumn("Écart", format="%.1f %%"),
                "run_id": "Exécution",
            },
        )

with tab_runs:
    st.dataframe(
        frames["runs"][
            [
                "id",
                "started_at",
                "mode",
                "status",
                "n_collected",
                "n_valid",
                "n_rejected",
                "n_stored",
                "n_alerts",
                "error",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "id": "#",
            "started_at": st.column_config.DatetimeColumn("Début", format="DD/MM/YYYY HH:mm"),
            "mode": "Mode",
            "status": "Statut",
            "n_collected": "Collectés",
            "n_valid": "Valides",
            "n_rejected": "Rejetés",
            "n_stored": "Stockés",
            "n_alerts": "Alertes",
            "error": "Détail",
        },
    )

with tab_report:
    st.write(
        "Le rapport reprend le résumé exécutif, les graphiques et les tableaux ci-dessus "
        "(toutes catégories). La version HTML est interactive."
    )
    if st.button("Générer le rapport", type="primary"):
        with st.spinner("Génération du rapport PDF et HTML…"):
            st.session_state["report_paths"] = generate_reports(repo, config)
    paths = st.session_state.get("report_paths")
    if paths:
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇️ Télécharger le PDF",
            paths["pdf"].read_bytes(),
            file_name=paths["pdf"].name,
            mime="application/pdf",
            use_container_width=True,
        )
        c2.download_button(
            "⬇️ Télécharger le HTML",
            paths["html"].read_bytes(),
            file_name=paths["html"].name,
            mime="text/html",
            use_container_width=True,
        )
    st.caption(
        "Dernier rapport publié automatiquement : "
        "[fleury-fcn.github.io/pricewatch-automation](https://fleury-fcn.github.io/pricewatch-automation/)"
    )
