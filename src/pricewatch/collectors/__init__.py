"""Connecteurs de collecte.

Ajouter une source = créer un module avec une sous-classe de :class:`BaseCollector`
décorée par ``@register_collector("nom")``, l'importer ici, puis la déclarer dans le YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pricewatch.collectors import books_toscrape  # noqa: F401  (enregistre le connecteur)
from pricewatch.collectors.base import (
    BaseCollector,
    RawProduct,
    available_collectors,
    get_collector_class,
    register_collector,
)
from pricewatch.collectors.http import PoliteHttpClient, RobotsDisallowedError
from pricewatch.config import AppConfig
from pricewatch.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class CollectionResult:
    """Résultat d'une collecte multi-sources."""

    products: list[RawProduct] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests: int = 0


def collect_all(config: AppConfig, http: PoliteHttpClient | None = None) -> CollectionResult:
    """Exécute tous les connecteurs actifs et agrège leurs résultats.

    Une source en échec complet est journalisée sans interrompre les autres.

    Args:
        config: Configuration de l'application.
        http: Client HTTP à réutiliser (sinon un client est créé puis fermé).
    """
    result = CollectionResult()
    client = http or PoliteHttpClient(config.collection)
    watch_urls = [w.url for w in config.watchlist]
    try:
        for source in config.enabled_sources:
            try:
                collector_cls = get_collector_class(source.connector)
            except KeyError as exc:
                result.errors.append(str(exc))
                logger.error("%s", exc)
                continue
            collector = collector_cls(source, client, watch_urls=watch_urls)
            try:
                before = len(result.products)
                result.products.extend(collector.collect())
                logger.info(
                    "Source %s : %d produits collectés", source.name, len(result.products) - before
                )
            except Exception as exc:  # une source défaillante ne doit pas tout arrêter
                logger.exception("Échec de la source %s", source.name)
                result.errors.append(f"{source.name}: {exc}")
            result.errors.extend(collector.errors)
    finally:
        result.requests = client.request_count
        if http is None:
            client.close()
    return result


__all__ = [
    "BaseCollector",
    "CollectionResult",
    "PoliteHttpClient",
    "RawProduct",
    "RobotsDisallowedError",
    "available_collectors",
    "collect_all",
    "get_collector_class",
    "register_collector",
]
