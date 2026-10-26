"""Orchestration de bout en bout : collecte → nettoyage → stockage → analyse → alertes → rapport."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pricewatch.alerts import build_alerts, build_notifiers, dispatch
from pricewatch.analysis import compute_product_stats
from pricewatch.cleaning import clean_products
from pricewatch.collectors import CollectionResult, PoliteHttpClient, RawProduct, collect_all
from pricewatch.config import AppConfig, Secrets, load_secrets
from pricewatch.logging_setup import get_logger
from pricewatch.reporting import generate_reports
from pricewatch.simulation import apply_simulation
from pricewatch.storage import AlertRecord, PriceRepository

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    """Bilan d'une exécution du pipeline."""

    run_id: int
    status: str
    mode: str
    n_collected: int = 0
    n_valid: int = 0
    n_rejected: int = 0
    n_stored: int = 0
    alerts: list[AlertRecord] = field(default_factory=list)
    delivered_to: list[str] = field(default_factory=list)
    reports: dict[str, Path] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def simulated_sources(config: AppConfig) -> set[str]:
    """Noms des sources actives pour lesquelles les prix sont simulés (mode démo)."""
    return {s.name for s in config.enabled_sources if s.simulate_price_moves}


def run_pipeline(
    config: AppConfig,
    repo: PriceRepository | None = None,
    *,
    send_alerts: bool = True,
    make_report: bool = True,
    products: Sequence[RawProduct] | None = None,
    http: PoliteHttpClient | None = None,
    secrets: Secrets | None = None,
    report_dir: Path | None = None,
) -> PipelineResult:
    """Exécute le pipeline complet et trace l'exécution en base.

    Args:
        config: Configuration de l'application.
        repo: Dépôt de données (créé depuis la configuration si ``None``).
        send_alerts: Envoie les alertes sur les canaux configurés.
        make_report: Génère les rapports HTML/PDF.
        products: Enregistrements bruts déjà collectés (court-circuite la collecte réseau).
        http: Client HTTP à utiliser pour la collecte.
        secrets: Secrets des notifieurs (lus depuis l'environnement si ``None``).
        report_dir: Dossier de sortie des rapports (sinon celui de la configuration).

    Returns:
        Le bilan de l'exécution.

    Raises:
        Exception: Toute erreur inattendue est journalisée, l'exécution marquée
            ``failed`` en base, puis l'erreur est relancée.
    """
    started = time.perf_counter()
    repo = repo or PriceRepository.from_url(config.storage.database_url)
    repo.init_db()
    sim = simulated_sources(config)
    mode = "demo" if sim else "live"
    run_id = repo.start_run(mode=mode)
    result = PipelineResult(run_id=run_id, status="running", mode=mode)
    logger.info("Exécution #%d démarrée (mode %s)", run_id, mode)
    try:
        if products is None:
            collection = collect_all(config, http)
        else:
            collection = CollectionResult(products=list(products))
        result.errors = list(collection.errors)
        result.n_collected = len(collection.products)

        cleaned = clean_products(collection.products, config.cleaning)
        result.n_valid = cleaned.report.n_valid
        result.n_rejected = cleaned.report.n_rejected
        data = apply_simulation(cleaned.data, sim, config.cleaning.exchange_rates)
        result.n_stored = repo.save_observations(data, run_id)

        observed = set(data["url"]) if not data.empty else set()
        if observed:
            history = repo.get_price_history()
            stats = compute_product_stats(history, config.analysis, config.target_prices())
            stats = stats[stats["url"].isin(observed)]
            since = datetime.now(UTC) - timedelta(hours=config.alerts.cooldown_hours)
            recent = repo.get_alerts(limit=10_000, since=since)
            recent_pairs = zip(recent["product_id"], recent["kind"], strict=True)
            result.alerts = build_alerts(stats, config.alerts, recent_pairs)
        if result.alerts and send_alerts:
            notifiers = build_notifiers(config.alerts.channels, secrets or load_secrets())
            result.delivered_to = dispatch(result.alerts, notifiers)
        repo.save_alerts(result.alerts, run_id, ",".join(result.delivered_to) or None)

        if result.n_valid == 0:
            result.status = "failed"
        elif result.errors or result.n_rejected:
            result.status = "partial"
        else:
            result.status = "success"
        repo.finish_run(
            run_id,
            result.status,
            n_collected=result.n_collected,
            n_valid=result.n_valid,
            n_rejected=result.n_rejected,
            n_stored=result.n_stored,
            n_alerts=len(result.alerts),
            n_requests=collection.requests,
            error="\n".join(result.errors[:20]) or None,
        )
        if make_report:
            result.reports = generate_reports(repo, config, report_dir)
    except Exception as exc:
        logger.exception("Exécution #%d en échec", run_id)
        repo.finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        result.duration_s = round(time.perf_counter() - started, 2)
    logger.info(
        "Exécution #%d terminée : %s — %d collectés, %d valides, %d stockés, %d alertes (%.1f s)",
        run_id,
        result.status,
        result.n_collected,
        result.n_valid,
        result.n_stored,
        len(result.alerts),
        result.duration_s,
    )
    return result
