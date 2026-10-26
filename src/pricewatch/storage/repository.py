"""Accès aux données : création du moteur, écriture des observations, requêtes d'historique."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, event, func, insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from pricewatch.logging_setup import get_logger
from pricewatch.storage.models import Alert, Base, PriceRecord, Product, Run, utcnow

logger = get_logger(__name__)

HISTORY_COLUMNS = [
    "product_id",
    "source",
    "url",
    "title",
    "category",
    "price",
    "currency",
    "price_base",
    "in_stock",
    "stock_qty",
    "rating",
    "collected_at",
]
ALERT_COLUMNS = [
    "id",
    "run_id",
    "product_id",
    "title",
    "url",
    "kind",
    "message",
    "price",
    "reference_price",
    "change_pct",
    "created_at",
    "delivered_to",
]


@dataclass(frozen=True)
class AlertRecord:
    """Alerte à persister (indépendante de l'ORM)."""

    product_id: int
    kind: str
    message: str
    price: float
    reference_price: float | None = None
    change_pct: float | None = None


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Crée un moteur SQLAlchemy ; pour SQLite, crée le dossier et active les clés étrangères."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, echo=echo)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def _clean(value: Any) -> Any:
    """Convertit NaN/NA pandas en None et les scalaires numpy en types Python."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):  # scalaires numpy
        return value.item()
    return value


class PriceRepository:
    """Point d'accès unique à la base de données.

    Args:
        engine: Moteur SQLAlchemy (voir :func:`create_db_engine`).
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, database_url: str) -> PriceRepository:
        """Crée un dépôt à partir d'une URL de base de données."""
        return cls(create_db_engine(database_url))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Session transactionnelle (commit si succès, rollback sinon)."""
        with self._sessions.begin() as session:
            yield session

    def init_db(self) -> None:
        """Crée les tables et index manquants (idempotent)."""
        Base.metadata.create_all(self.engine)

    def reset_db(self) -> None:
        """Supprime puis recrée toutes les tables."""
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)

    # -- exécutions -------------------------------------------------------------
    def start_run(self, mode: str = "live") -> int:
        """Enregistre le début d'une exécution et retourne son identifiant."""
        with self.session() as s:
            run = Run(mode=mode, started_at=utcnow())
            s.add(run)
            s.flush()
            return run.id

    def finish_run(self, run_id: int, status: str, **stats: Any) -> None:
        """Clôture une exécution avec son statut et ses compteurs."""
        with self.session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(f"Exécution inconnue : {run_id}")
            run.status = status
            run.finished_at = utcnow()
            for key, value in stats.items():
                if not hasattr(run, key):
                    raise AttributeError(f"Champ d'exécution inconnu : {key}")
                setattr(run, key, value)

    def get_runs(self, limit: int = 20) -> pd.DataFrame:
        """Dernières exécutions, de la plus récente à la plus ancienne."""
        stmt = select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
        with self.session() as s:
            rows = [
                {c.key: getattr(run, c.key) for c in Run.__table__.columns}
                for run in s.scalars(stmt)
            ]
        return pd.DataFrame(rows, columns=[c.key for c in Run.__table__.columns])

    # -- écriture des observations ----------------------------------------------
    def save_observations(self, data: pd.DataFrame, run_id: int | None = None) -> int:
        """Crée/met à jour les produits et ajoute les observations de prix.

        Les observations déjà présentes (même produit, même horodatage) sont ignorées,
        ce qui rend l'opération idempotente.

        Args:
            data: DataFrame au schéma :class:`~pricewatch.schemas.CleanProduct`.
            run_id: Exécution à laquelle rattacher les observations.

        Returns:
            Le nombre d'observations réellement insérées.
        """
        if data.empty:
            return 0
        records = [{k: _clean(v) for k, v in row.items()} for row in data.to_dict("records")]
        with self.session() as s:
            ids = self._upsert_products(s, records)
            rows = [
                {
                    "product_id": ids[r["url"]],
                    "run_id": run_id,
                    "price": float(r["price"]),
                    "currency": r["currency"],
                    "price_base": r.get("price_base"),
                    "in_stock": None if r.get("in_stock") is None else bool(r["in_stock"]),
                    "stock_qty": r.get("stock_qty"),
                    "rating": r.get("rating"),
                    "collected_at": r["collected_at"],
                }
                for r in records
            ]
            count = select(func.count(PriceRecord.id)).where(
                PriceRecord.product_id.in_(set(ids.values()))
            )
            before = int(s.scalar(count) or 0)
            for chunk in _chunks(rows, 500):
                s.execute(self._insert_ignore(PriceRecord), chunk)
            inserted = int(s.scalar(count) or 0) - before
        logger.info("Stockage : %d nouvelles observations sur %d", inserted, len(records))
        return inserted

    def _insert_ignore(self, model: type[Base]) -> Any:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            return sqlite.insert(model).on_conflict_do_nothing()
        if dialect == "postgresql":  # pragma: no cover - testé hors CI
            return postgresql.insert(model).on_conflict_do_nothing()
        return insert(model)  # pragma: no cover

    @staticmethod
    def _upsert_products(s: Session, records: Sequence[dict[str, Any]]) -> dict[str, int]:
        latest: dict[str, dict[str, Any]] = {}
        for r in sorted(records, key=lambda r: r["collected_at"]):
            latest[r["url"]] = r
        existing = {
            p.url: p for p in s.scalars(select(Product).where(Product.url.in_(list(latest))))
        }
        for url, r in latest.items():
            product = existing.get(url)
            seen_at = r["collected_at"]
            if product is None:
                product = Product(url=url, first_seen=seen_at, last_seen=seen_at)
                s.add(product)
                existing[url] = product
            elif seen_at > product.last_seen:
                product.last_seen = seen_at
            elif seen_at < product.first_seen:
                product.first_seen = seen_at
                continue  # observation ancienne (backfill) : ne pas écraser les métadonnées
            else:
                continue
            product.source = r["source"]
            product.title = r["title"]
            product.currency = r["currency"]
            product.category = r.get("category") or product.category
            product.external_id = r.get("external_id") or product.external_id
            product.image_url = r.get("image_url") or product.image_url
        s.flush()
        return {url: p.id for url, p in existing.items()}

    # -- lecture ---------------------------------------------------------------
    def get_price_history(
        self,
        product_ids: Iterable[int] | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        category: str | None = None,
    ) -> pd.DataFrame:
        """Historique des prix joint aux informations produit, trié chronologiquement.

        Args:
            product_ids: Restreint à ces produits.
            since: Observations postérieures ou égales à cette date.
            limit: Nombre maximum de lignes (les plus récentes).
            category: Restreint à une catégorie.
        """
        stmt = select(
            PriceRecord.product_id,
            Product.source,
            Product.url,
            Product.title,
            Product.category,
            PriceRecord.price,
            PriceRecord.currency,
            PriceRecord.price_base,
            PriceRecord.in_stock,
            PriceRecord.stock_qty,
            PriceRecord.rating,
            PriceRecord.collected_at,
        ).join(Product, Product.id == PriceRecord.product_id)
        if product_ids is not None:
            stmt = stmt.where(PriceRecord.product_id.in_(list(product_ids)))
        if since is not None:
            stmt = stmt.where(PriceRecord.collected_at >= since)
        if category is not None:
            stmt = stmt.where(Product.category == category)
        stmt = stmt.order_by(PriceRecord.collected_at.desc(), PriceRecord.product_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        with self.session() as s:
            rows = s.execute(stmt).all()
        df = pd.DataFrame([tuple(r) for r in rows], columns=HISTORY_COLUMNS)
        if not df.empty:
            df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)
            df = df.sort_values(["collected_at", "product_id"], kind="stable")
        return df.reset_index(drop=True)

    def get_products(self) -> pd.DataFrame:
        """Produits avec dernier prix connu et nombre d'observations."""
        last = (
            select(
                PriceRecord.product_id,
                func.max(PriceRecord.collected_at).label("last_at"),
                func.count(PriceRecord.id).label("n_observations"),
            )
            .group_by(PriceRecord.product_id)
            .subquery()
        )
        stmt = (
            select(
                Product.id,
                Product.source,
                Product.title,
                Product.category,
                Product.url,
                Product.external_id,
                Product.currency,
                Product.image_url,
                Product.first_seen,
                Product.last_seen,
                PriceRecord.price.label("last_price"),
                last.c.n_observations,
            )
            .join(last, last.c.product_id == Product.id)
            .join(
                PriceRecord,
                (PriceRecord.product_id == Product.id)
                & (PriceRecord.collected_at == last.c.last_at),
            )
            .order_by(Product.title)
        )
        with self.session() as s:
            result = s.execute(stmt)
            columns = list(result.keys())
            rows = result.all()
        return pd.DataFrame([tuple(r) for r in rows], columns=columns)

    def count_observations(self) -> int:
        """Nombre total d'observations de prix."""
        with self.session() as s:
            return int(s.scalar(select(func.count(PriceRecord.id))) or 0)

    # -- alertes ---------------------------------------------------------------
    def save_alerts(
        self, alerts: Iterable[AlertRecord], run_id: int | None, delivered_to: str | None = None
    ) -> int:
        """Persiste des alertes et retourne leur nombre."""
        rows = [
            Alert(
                run_id=run_id,
                product_id=a.product_id,
                kind=a.kind,
                message=a.message,
                price=a.price,
                reference_price=a.reference_price,
                change_pct=a.change_pct,
                delivered_to=delivered_to,
            )
            for a in alerts
        ]
        with self.session() as s:
            s.add_all(rows)
        return len(rows)

    def get_alerts(
        self, limit: int = 50, kind: str | None = None, since: datetime | None = None
    ) -> pd.DataFrame:
        """Dernières alertes avec le titre et l'URL du produit."""
        stmt = (
            select(
                Alert.id,
                Alert.run_id,
                Alert.product_id,
                Product.title,
                Product.url,
                Alert.kind,
                Alert.message,
                Alert.price,
                Alert.reference_price,
                Alert.change_pct,
                Alert.created_at,
                Alert.delivered_to,
            )
            .join(Product, Product.id == Alert.product_id)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
            .limit(limit)
        )
        if kind is not None:
            stmt = stmt.where(Alert.kind == kind)
        if since is not None:
            stmt = stmt.where(Alert.created_at >= since)
        with self.session() as s:
            rows = s.execute(stmt).all()
        return pd.DataFrame([tuple(r) for r in rows], columns=ALERT_COLUMNS)


def _chunks(items: Sequence[dict[str, Any]], size: int) -> Iterator[Sequence[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
