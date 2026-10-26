"""Connecteur pour https://books.toscrape.com, bac à sable légal dédié au scraping."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from pricewatch.collectors.base import BaseCollector, RawProduct, register_collector
from pricewatch.collectors.http import RobotsDisallowedError
from pricewatch.logging_setup import get_logger

logger = get_logger(__name__)

_RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
_UPC_LABEL = "UPC"
# Le site ne déclare pas de charset dans l'en-tête HTTP : on passe toujours les octets
# (``response.content``) à BeautifulSoup, qui lit la balise <meta charset> et évite « Â£ ».


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def _rating(node: Tag | None) -> int | None:
    if node is None:
        return None
    classes: list[str] = list(node.get("class") or [])
    return next((_RATINGS[c] for c in classes if c in _RATINGS), None)


def parse_listing(
    html: bytes | str, page_url: str, source: str
) -> tuple[list[RawProduct], str | None]:
    """Extrait les produits d'une page de liste et l'URL de la page suivante.

    Args:
        html: Contenu HTML de la page.
        page_url: URL de la page (pour résoudre les liens relatifs).
        source: Nom de la source à inscrire dans chaque enregistrement.

    Returns:
        Un tuple ``(produits, url_page_suivante_ou_None)``.
    """
    soup = BeautifulSoup(html, "lxml")
    products: list[RawProduct] = []
    for pod in soup.select("article.product_pod"):
        link = pod.select_one("h3 a")
        price = pod.select_one("p.price_color")
        if link is None or price is None or not link.get("href"):
            logger.warning("Fiche produit incomplète ignorée sur %s", page_url)
            continue
        image = pod.select_one("div.image_container img")
        products.append(
            RawProduct(
                source=source,
                url=urljoin(page_url, str(link["href"])),
                title=str(link.get("title") or _text(link) or ""),
                price_raw=_text(price) or "",
                availability_raw=_text(pod.select_one("p.availability")),
                rating=_rating(pod.select_one("p.star-rating")),
                image_url=urljoin(page_url, str(image["src"])) if image else None,
            )
        )
    next_link = soup.select_one("li.next a")
    next_url = urljoin(page_url, str(next_link["href"])) if next_link else None
    return products, next_url


def parse_detail(html: bytes | str, url: str, source: str) -> RawProduct:
    """Extrait un produit depuis sa page détaillée (UPC, catégorie, stock précis).

    Raises:
        ValueError: Si le titre ou le prix est introuvable.
    """
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("div.product_main")
    title = _text(main.select_one("h1")) if main else None
    price = _text(main.select_one("p.price_color")) if main else None
    if not title or not price:
        raise ValueError(f"Titre ou prix introuvable sur {url}")
    table = {
        _text(row.select_one("th")): _text(row.select_one("td"))
        for row in soup.select("table.table tr")
    }
    crumbs = [_text(li) for li in soup.select("ul.breadcrumb li")]
    # Fil d'Ariane : Home > Books > <Catégorie> > <Titre> ; « Default » = livre non classé
    category = crumbs[2] if len(crumbs) >= 4 else None
    if category == "Default":
        category = None
    image = soup.select_one("#product_gallery img")
    return RawProduct(
        source=source,
        url=url,
        title=title,
        price_raw=price,
        availability_raw=_text(main.select_one("p.availability")) if main else None,
        category=category,
        external_id=table.get(_UPC_LABEL),
        rating=_rating(main.select_one("p.star-rating")) if main else None,
        image_url=urljoin(url, str(image["src"])) if image else None,
    )


@register_collector("books_toscrape")
class BooksToScrapeCollector(BaseCollector):
    """Parcourt le catalogue (ou des catégories) puis, si ``fetch_details`` est actif
    (par défaut), visite chaque fiche pour obtenir UPC, catégorie et stock.

    Options YAML (``options``) : ``fetch_details`` (bool, défaut ``True``).
    """

    def _start_urls(self) -> list[str]:
        base = self.source.base_url
        if self.source.categories:
            return [
                urljoin(base, f"catalogue/category/books/{slug}/index.html")
                for slug in self.source.categories
            ]
        return [urljoin(base, "catalogue/page-1.html")]

    def _fetch(self, url: str) -> httpx.Response | None:
        try:
            return self.http.get(url)
        except (httpx.HTTPError, RobotsDisallowedError) as exc:
            message = f"{url}: {exc}"
            self.errors.append(message)
            logger.error("Échec de récupération %s", message)
            return None

    def _listing(self) -> Iterator[RawProduct]:
        for start in self._start_urls():
            url: str | None = start
            pages = 0
            while url and pages < self.source.max_pages:
                response = self._fetch(url)
                if response is None:
                    break
                products, url = parse_listing(response.content, str(response.url), self.source.name)
                pages += 1
                logger.info("%s : page %d, %d produits", self.source.name, pages, len(products))
                yield from products

    def _detail(self, url: str, fallback: RawProduct | None = None) -> RawProduct | None:
        response = self._fetch(url)
        if response is None:
            return fallback
        try:
            detail = parse_detail(response.content, url, self.source.name)
        except ValueError as exc:
            self.errors.append(str(exc))
            logger.error("%s", exc)
            return fallback
        if fallback is not None and detail.rating is None:
            # La note n'est pas toujours exposée de la même façon : on garde celle de la liste.
            detail = RawProduct(**{**detail.to_dict(), "rating": fallback.rating})
        return detail

    def collect(self) -> Iterator[RawProduct]:
        """Collecte la liste paginée puis les produits suivis absents de la liste."""
        fetch_details = bool(self.source.options.get("fetch_details", True))
        seen: set[str] = set()
        for item in self._listing():
            if item.url in seen:
                continue
            seen.add(item.url)
            product = self._detail(item.url, fallback=item) if fetch_details else item
            if product is not None:
                yield product
        for url in self.watch_urls:
            if url not in seen and url.startswith(self.source.base_url):
                seen.add(url)
                if (product := self._detail(url)) is not None:
                    yield product


__all__ = ["BooksToScrapeCollector", "parse_detail", "parse_listing"]
