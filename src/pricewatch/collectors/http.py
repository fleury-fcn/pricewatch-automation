"""Client HTTP « poli » : robots.txt, délai entre requêtes, User-Agent, retries avec backoff."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from pricewatch.config import CollectionSettings
from pricewatch.logging_setup import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RobotsDisallowedError(RuntimeError):
    """L'URL demandée est interdite par le robots.txt du site."""


class PoliteHttpClient:
    """Enveloppe ``httpx.Client`` avec les bonnes pratiques de collecte.

    - vérifie ``robots.txt`` (une fois par hôte, mis en cache) ;
    - respecte un délai minimal entre deux requêtes vers un même hôte (ou le
      ``Crawl-delay`` du site s'il est plus grand) ;
    - s'identifie avec un User-Agent explicite ;
    - réessaie les erreurs réseau et les statuts 429/5xx avec un backoff exponentiel,
      en honorant l'en-tête ``Retry-After`` lorsqu'il est présent.

    Args:
        settings: Paramètres de collecte.
        client: Client httpx injecté (tests) ; un client est créé sinon.
        sleep: Fonction d'attente injectable (tests).
        clock: Horloge monotone injectable (tests).
    """

    def __init__(
        self,
        settings: CollectionSettings,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )
        self._client.headers["User-Agent"] = settings.user_agent
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
        self.request_count = 0

    # -- robots.txt ---------------------------------------------------------------
    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser = RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        parser.set_url(robots_url)
        try:
            response = self._send(robots_url)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt inaccessible (%s) : collecte autorisée par défaut", exc)
            parser.parse([])
        else:
            # Même sémantique que urllib.robotparser.RobotFileParser.read()
            if response.status_code in (401, 403):
                parser.disallow_all = True  # type: ignore[attr-defined]  # attribut non typé
            elif response.status_code >= 400:
                parser.allow_all = True  # type: ignore[attr-defined]
            else:
                parser.parse(response.text.splitlines())
        # RobotFileParser.can_fetch() renvoie toujours False tant que last_checked vaut 0.
        parser.modified()
        self._robots[origin] = parser
        return parser

    def is_allowed(self, url: str) -> bool:
        """Indique si ``robots.txt`` autorise la récupération de ``url``."""
        if not self.settings.respect_robots_txt:
            return True
        return self._robots_for(url).can_fetch(self.settings.user_agent, url)

    def _min_delay(self, url: str) -> float:
        delay = self.settings.delay_seconds
        if self.settings.respect_robots_txt:
            crawl_delay = self._robots_for(url).crawl_delay(self.settings.user_agent)
            if crawl_delay:
                delay = max(delay, float(crawl_delay))
        return delay

    # -- envoi -------------------------------------------------------------------
    def _throttle(self, host: str, delay: float) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return self.settings.backoff_factor * (2**attempt)

    def _send(self, url: str, delay: float | None = None) -> httpx.Response:
        host = urlsplit(url).netloc
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            self._throttle(host, self.settings.delay_seconds if delay is None else delay)
            response: httpx.Response | None = None
            try:
                self.request_count += 1
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt == attempts - 1:
                    raise
                logger.warning("Erreur réseau sur %s (%s), tentative %d", url, exc, attempt + 1)
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == attempts - 1:
                    return response
                logger.warning(
                    "Statut %d sur %s, tentative %d", response.status_code, url, attempt + 1
                )
            self._sleep(self._backoff(attempt, response))
        raise AssertionError("unreachable")  # pragma: no cover

    def get(self, url: str) -> httpx.Response:
        """Récupère ``url`` en appliquant robots.txt, throttling et retries.

        Raises:
            RobotsDisallowedError: Si robots.txt interdit l'URL.
            httpx.HTTPStatusError: Si la réponse finale est en erreur (4xx/5xx).
            httpx.TransportError: Si le réseau échoue après toutes les tentatives.
        """
        if not self.is_allowed(url):
            raise RobotsDisallowedError(f"robots.txt interdit {url}")
        response = self._send(url, delay=self._min_delay(url))
        response.raise_for_status()
        logger.debug("GET %s -> %d", url, response.status_code)
        return response

    # -- cycle de vie --------------------------------------------------------------
    def close(self) -> None:
        """Ferme le client sous-jacent s'il a été créé par cette instance."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
