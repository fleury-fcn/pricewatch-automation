"""Contrat commun des connecteurs et registre permettant d'en ajouter facilement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from pricewatch.collectors.http import PoliteHttpClient
from pricewatch.config import SourceConfig


@dataclass(frozen=True, slots=True)
class RawProduct:
    """Enregistrement brut tel qu'extrait d'une page, avant nettoyage.

    Les champs ``*_raw`` conservent le texte d'origine : la normalisation (prix, devise,
    disponibilité) est volontairement faite plus tard, dans l'étape de nettoyage.
    """

    source: str
    url: str
    title: str
    price_raw: str
    availability_raw: str | None = None
    category: str | None = None
    external_id: str | None = None
    rating: int | None = None
    image_url: str | None = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Représentation dictionnaire (pour construire un DataFrame)."""
        return asdict(self)


class BaseCollector(ABC):
    """Classe de base d'un connecteur de source.

    Un connecteur reçoit sa configuration et un client HTTP poli, et produit des
    :class:`RawProduct`. Il ne doit jamais interrompre toute la collecte pour une page
    en erreur : il journalise et continue.
    """

    connector: ClassVar[str]

    def __init__(
        self,
        source: SourceConfig,
        http: PoliteHttpClient,
        watch_urls: Sequence[str] = (),
    ) -> None:
        self.source = source
        self.http = http
        self.watch_urls = list(watch_urls)
        self.errors: list[str] = []

    @abstractmethod
    def collect(self) -> Iterator[RawProduct]:
        """Itère sur les produits collectés."""


_REGISTRY: dict[str, type[BaseCollector]] = {}


def register_collector(name: str) -> Callable[[type[BaseCollector]], type[BaseCollector]]:
    """Décorateur enregistrant un connecteur sous ``name`` (valeur ``connector`` du YAML)."""

    def decorator(cls: type[BaseCollector]) -> type[BaseCollector]:
        if name in _REGISTRY:
            raise ValueError(f"Connecteur déjà enregistré : {name}")
        cls.connector = name
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_collector_class(name: str) -> type[BaseCollector]:
    """Retourne la classe du connecteur ``name``.

    Raises:
        KeyError: Si aucun connecteur ne porte ce nom.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "aucun"
        raise KeyError(f"Connecteur inconnu « {name} » (disponibles : {known})") from None


def available_collectors() -> list[str]:
    """Noms des connecteurs enregistrés."""
    return sorted(_REGISTRY)
