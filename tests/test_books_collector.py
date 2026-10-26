from pathlib import Path

import httpx
import pytest
import respx

from pricewatch.collectors import (
    BaseCollector,
    PoliteHttpClient,
    available_collectors,
    collect_all,
    get_collector_class,
    register_collector,
)
from pricewatch.collectors.books_toscrape import (
    BooksToScrapeCollector,
    parse_detail,
    parse_listing,
)
from pricewatch.config import AppConfig, CollectionSettings, SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://books.toscrape.com/"
LIST_1 = (FIXTURES / "books_list_page1.html").read_bytes()
LIST_LAST = (FIXTURES / "books_list_last.html").read_bytes()
DETAIL = (FIXTURES / "books_detail.html").read_bytes()
CATEGORY = (FIXTURES / "books_category_travel.html").read_bytes()
HEADERS = {"Content-Type": "text/html"}  # comme le vrai site : pas de charset


def source(**overrides: object) -> SourceConfig:
    data = {"name": "books", "connector": "books_toscrape", "base_url": BASE, "max_pages": 2}
    return SourceConfig(**{**data, **overrides})


def http() -> PoliteHttpClient:
    return PoliteHttpClient(
        CollectionSettings(delay_seconds=0, max_retries=0), sleep=lambda s: None
    )


def test_parse_listing_first_page() -> None:
    products, next_url = parse_listing(LIST_1, f"{BASE}catalogue/page-1.html", "books")
    assert len(products) == 20
    first = products[0]
    assert first.title == "A Light in the Attic"
    assert first.price_raw == "£51.77"
    assert first.url == f"{BASE}catalogue/a-light-in-the-attic_1000/index.html"
    assert first.availability_raw == "In stock"
    assert first.rating == 3
    assert first.image_url and first.image_url.startswith(f"{BASE}media/")
    assert next_url == f"{BASE}catalogue/page-2.html"


def test_parse_listing_last_page_has_no_next() -> None:
    products, next_url = parse_listing(LIST_LAST, f"{BASE}catalogue/page-50.html", "books")
    assert len(products) == 20
    assert next_url is None


def test_parse_listing_skips_incomplete_pods() -> None:
    html = "<article class='product_pod'><h3><a href='x'>X</a></h3></article>"
    products, _ = parse_listing(html, BASE, "books")
    assert products == []


def test_parse_detail() -> None:
    url = f"{BASE}catalogue/a-light-in-the-attic_1000/index.html"
    product = parse_detail(DETAIL, url, "books")
    assert product.title == "A Light in the Attic"
    assert product.external_id == "a897fe39b1053632"
    assert product.category == "Poetry"
    assert product.availability_raw == "In stock (22 available)"
    assert product.price_raw == "£51.77"


def test_parse_detail_invalid_page() -> None:
    with pytest.raises(ValueError):
        parse_detail("<html><body>vide</body></html>", BASE, "books")


@respx.mock
def test_collector_paginates_without_details() -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(200, content=LIST_1, headers=HEADERS)
    respx.get(f"{BASE}catalogue/page-2.html").respond(200, content=LIST_LAST, headers=HEADERS)
    collector = BooksToScrapeCollector(source(options={"fetch_details": False}), http())
    products = list(collector.collect())
    assert len(products) == 40
    assert products[0].price_raw == "£51.77"  # pas de « Â£ » malgré l'absence de charset
    assert collector.errors == []


@respx.mock
def test_collector_respects_max_pages() -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(200, content=LIST_1, headers=HEADERS)
    page2 = respx.get(f"{BASE}catalogue/page-2.html").respond(200, content=LIST_LAST)
    collector = BooksToScrapeCollector(
        source(max_pages=1, options={"fetch_details": False}), http()
    )
    assert len(list(collector.collect())) == 20
    assert not page2.called


@respx.mock
def test_collector_with_details_and_watchlist() -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/category/books/travel_2/index.html").respond(
        200, content=CATEGORY, headers=HEADERS
    )
    details = respx.get(url__regex=r".*/catalogue/[^/]+/index\.html$").respond(
        200, content=DETAIL, headers=HEADERS
    )
    watched = f"{BASE}catalogue/watched-book_1/index.html"
    collector = BooksToScrapeCollector(
        source(categories=["travel_2"]),
        http(),
        watch_urls=[watched, "https://other.site/x"],
    )
    products = list(collector.collect())
    assert len(products) == 12  # 11 livres de la catégorie + 1 produit suivi
    assert details.call_count == 12
    assert all(p.url.startswith(f"{BASE}catalogue/") for p in products)
    assert "/category/" not in products[0].url
    assert all(p.category == "Poetry" for p in products)
    assert products[-1].url == watched
    assert products[0].rating == 3  # note conservée depuis la liste si absente du détail


@respx.mock
def test_collector_records_errors_and_falls_back() -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(200, content=LIST_LAST, headers=HEADERS)
    respx.get(url__regex=r".*/catalogue/[^/]+/index\.html$").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, text="<html></html>")]
        + [httpx.Response(200, content=DETAIL)] * 18
    )
    collector = BooksToScrapeCollector(source(max_pages=1), http())
    products = list(collector.collect())
    assert len(products) == 20  # les 2 échecs retombent sur les données de la liste
    assert len(collector.errors) == 2
    assert products[0].category is None


@respx.mock
def test_collector_stops_when_listing_fails() -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(503)
    collector = BooksToScrapeCollector(source(), http())
    assert list(collector.collect()) == []
    assert collector.errors and "503" in collector.errors[0]


def test_registry() -> None:
    assert "books_toscrape" in available_collectors()
    assert get_collector_class("books_toscrape") is BooksToScrapeCollector
    with pytest.raises(KeyError, match="inconnu"):
        get_collector_class("nope")
    with pytest.raises(ValueError):
        register_collector("books_toscrape")(BooksToScrapeCollector)


@respx.mock
def test_collect_all_aggregates_and_isolates_failures() -> None:
    @register_collector("exploding_test")
    class Exploding(BaseCollector):
        def collect(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("panne")
            yield  # pragma: no cover

    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(200, content=LIST_LAST, headers=HEADERS)
    config = AppConfig.model_validate(
        {
            "collection": {"delay_seconds": 0, "max_retries": 0},
            "sources": [
                {
                    "name": "books",
                    "connector": "books_toscrape",
                    "base_url": BASE,
                    "options": {"fetch_details": False},
                },
                {"name": "ghost", "connector": "unknown", "base_url": BASE},
                {"name": "boom", "connector": "exploding_test", "base_url": BASE},
                {"name": "off", "connector": "books_toscrape", "base_url": BASE, "enabled": False},
            ],
        }
    )
    result = collect_all(config)
    assert len(result.products) == 20
    assert result.requests == 2
    assert any("inconnu" in e for e in result.errors)
    assert any("panne" in e for e in result.errors)


@pytest.mark.network
def test_live_books_toscrape() -> None:  # pragma: no cover - exécuté manuellement
    collector = BooksToScrapeCollector(
        source(max_pages=1, options={"fetch_details": False}),
        PoliteHttpClient(CollectionSettings()),
    )
    assert len(list(collector.collect())) == 20


def test_default_category_means_uncategorised() -> None:
    html = DETAIL.replace(b">Poetry</a>", b">Default</a>")
    assert parse_detail(html, BASE, "books").category is None
