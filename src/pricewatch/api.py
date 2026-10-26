"""API REST (FastAPI). Documentation interactive : ``/docs`` (Swagger) et ``/redoc``."""

from __future__ import annotations

import math
import os
import secrets as pysecrets
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from pricewatch import __version__
from pricewatch.analysis import compute_product_stats
from pricewatch.config import AppConfig, load_config
from pricewatch.logging_setup import get_logger, setup_logging
from pricewatch.storage import PriceRepository

logger = get_logger(__name__)


# -- schémas de réponse ----------------------------------------------------------
class Health(BaseModel):
    """État du service."""

    status: Literal["ok"] = "ok"
    version: str
    products: int
    observations: int
    pipeline_running: bool


class ProductOut(BaseModel):
    """Produit suivi et son dernier prix."""

    id: int
    source: str
    title: str
    category: str | None
    url: str
    currency: str
    last_price: float
    n_observations: int
    first_seen: datetime
    last_seen: datetime


class PriceOut(BaseModel):
    """Observation de prix."""

    product_id: int
    title: str
    category: str | None
    price: float
    currency: str
    price_base: float | None
    in_stock: bool | None
    collected_at: datetime


class StatsOut(BaseModel):
    """Indicateurs d'analyse d'un produit."""

    product_id: int
    title: str
    category: str | None
    currency: str
    n_obs: int
    last_price: float
    reference_price: float | None
    drop_pct: float | None
    change_pct: float | None
    trend: str
    trend_pct_per_day: float
    zscore: float
    is_anomaly: bool
    is_significant_drop: bool
    target_reached: bool


class AlertOut(BaseModel):
    """Alerte émise."""

    id: int
    run_id: int | None
    product_id: int
    title: str
    kind: str
    message: str
    price: float
    reference_price: float | None
    change_pct: float | None
    created_at: datetime
    delivered_to: str | None


class RunOut(BaseModel):
    """Exécution du pipeline."""

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    mode: str
    n_collected: int
    n_valid: int
    n_rejected: int
    n_stored: int
    n_alerts: int
    error: str | None


class RunRequest(BaseModel):
    """Options d'une exécution déclenchée par l'API."""

    send_alerts: bool = Field(True, description="Envoyer les alertes sur les canaux configurés")
    make_report: bool = Field(True, description="Régénérer les rapports HTML/PDF")
    max_pages: int | None = Field(None, ge=1, le=50, description="Limite de pages par source")


class RunAccepted(BaseModel):
    """Accusé de réception d'une exécution lancée en arrière-plan."""

    message: str
    status_url: str = "/runs?limit=1"


# -- utilitaires -----------------------------------------------------------------
def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → liste de dicts JSON-compatibles (NaN/NA → None)."""
    out: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if (
                value is None
                or (isinstance(value, float) and math.isnan(value))
                or value is pd.NA
                or value is pd.NaT
            ):
                clean[key] = None
            elif isinstance(value, pd.Timestamp):
                clean[key] = value.to_pydatetime()
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        out.append(clean)
    return out


class _PipelineGuard:
    """Empêche deux exécutions simultanées du pipeline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def try_acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()


def get_repo(request: Request) -> PriceRepository:
    """Dépendance FastAPI : dépôt de données de l'application."""
    repo: PriceRepository = request.app.state.repo
    return repo


Repo = Annotated[PriceRepository, Depends(get_repo)]


def create_app(
    config: AppConfig | None = None,
    repo: PriceRepository | None = None,
    pipeline_runner: Callable[..., Any] | None = None,
) -> FastAPI:
    """Fabrique de l'application (injectable pour les tests).

    Args:
        config: Configuration (chargée depuis ``PRICEWATCH_CONFIG`` si ``None``).
        repo: Dépôt de données (créé depuis la configuration si ``None``).
        pipeline_runner: Fonction exécutant le pipeline (``run_pipeline`` par défaut).
    """
    config = config or load_config()
    repo = repo or PriceRepository.from_url(config.storage.database_url)
    guard = _PipelineGuard()
    api_key = os.environ.get("PRICEWATCH_API_KEY")

    if pipeline_runner is None:
        from pricewatch.pipeline import run_pipeline

        pipeline_runner = run_pipeline

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        setup_logging(config.logging)
        repo.init_db()
        yield

    app = FastAPI(
        title="PriceWatch API",
        version=__version__,
        description=(
            "Suivi automatisé de prix e-commerce : produits, historique des prix, "
            "indicateurs, alertes et déclenchement du pipeline."
        ),
        lifespan=lifespan,
    )
    app.state.repo = repo
    app.state.guard = guard

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if api_key and not (x_api_key and pysecrets.compare_digest(x_api_key, api_key)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé API invalide ou absente")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    @app.get("/health", response_model=Health, tags=["système"])
    def health(r: Repo) -> Health:
        """État du service et volumétrie."""
        return Health(
            version=__version__,
            products=len(r.get_products()),
            observations=r.count_observations(),
            pipeline_running=guard.running,
        )

    @app.get("/products", response_model=list[ProductOut], tags=["données"])
    def products(r: Repo, category: str | None = None) -> list[dict[str, Any]]:
        """Produits suivis avec leur dernier prix."""
        df = r.get_products()
        if category is not None:
            df = df[df["category"] == category]
        return _records(df)

    @app.get("/prices", response_model=list[PriceOut], tags=["données"])
    def prices(
        r: Repo,
        product_id: Annotated[list[int] | None, Query()] = None,
        category: str | None = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
    ) -> list[dict[str, Any]]:
        """Historique des prix (les ``limit`` observations les plus récentes, ordre chronologique)."""
        df = r.get_price_history(
            product_ids=product_id, since=since, limit=limit, category=category
        )
        return _records(df)

    @app.get("/stats", response_model=list[StatsOut], tags=["analyse"])
    def stats(
        r: Repo,
        only: Annotated[
            Literal["all", "drops", "anomalies", "targets"], Query(description="Filtre")
        ] = "all",
    ) -> list[dict[str, Any]]:
        """Indicateurs par produit : écart à la moyenne, tendance, z-score…"""
        df = compute_product_stats(r.get_price_history(), config.analysis, config.target_prices())
        filters = {
            "drops": "is_significant_drop",
            "anomalies": "is_anomaly",
            "targets": "target_reached",
        }
        if only in filters and not df.empty:
            df = df[df[filters[only]]]
        return _records(df.sort_values("drop_pct", na_position="last") if not df.empty else df)

    @app.get("/alerts", response_model=list[AlertOut], tags=["analyse"])
    def alerts(
        r: Repo,
        kind: Literal["price_drop", "target_reached", "anomaly"] | None = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    ) -> list[dict[str, Any]]:
        """Dernières alertes émises."""
        return _records(r.get_alerts(limit=limit, kind=kind, since=since))

    @app.get("/runs", response_model=list[RunOut], tags=["pipeline"])
    def runs(r: Repo, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
        """Historique des exécutions du pipeline."""
        return _records(r.get_runs(limit))

    @app.post(
        "/run",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["pipeline"],
        dependencies=[Depends(require_key)],
        responses={409: {"description": "Une exécution est déjà en cours"}},
    )
    def trigger_run(
        background: BackgroundTasks, r: Repo, body: RunRequest | None = None
    ) -> RunAccepted:
        """Lance le pipeline en arrière-plan (protégé par ``X-API-Key`` si configuré)."""
        body = body or RunRequest()
        if not guard.try_acquire():
            raise HTTPException(status.HTTP_409_CONFLICT, "Une exécution est déjà en cours")

        def job() -> None:
            try:
                run_config = config.model_copy(deep=True)
                if body.max_pages is not None:
                    for source in run_config.sources:
                        source.max_pages = body.max_pages
                pipeline_runner(
                    run_config, r, send_alerts=body.send_alerts, make_report=body.make_report
                )
            except Exception:  # déjà journalisé et tracé en base par le pipeline
                logger.exception("Exécution déclenchée par l'API en échec")
            finally:
                guard.release()

        background.add_task(job)
        return RunAccepted(message="Exécution du pipeline lancée")

    @app.get("/reports/latest.{fmt}", tags=["rapports"], response_class=FileResponse)
    def latest_report(fmt: Literal["html", "pdf"]) -> FileResponse:
        """Télécharge le dernier rapport généré."""
        path = Path(config.report.output_dir) / f"latest.{fmt}"
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucun rapport généré pour l'instant")
        media = "application/pdf" if fmt == "pdf" else "text/html"
        return FileResponse(path, media_type=media, filename=f"pricewatch_latest.{fmt}")

    return app
