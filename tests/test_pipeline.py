"""Tests d'intégration : pipeline complet (réseau simulé par respx)."""

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
import respx

from pricewatch import demo
from pricewatch.config import AppConfig, Secrets
from pricewatch.pipeline import run_pipeline, simulated_sources
from pricewatch.storage import PriceRepository

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://books.toscrape.com/"
HEADERS = {"Content-Type": "text/html"}


def repo_for(config: AppConfig) -> PriceRepository:
    return PriceRepository.from_url(config.storage.database_url)


@respx.mock
def test_full_pipeline_with_mocked_http(test_config: AppConfig) -> None:
    respx.get(f"{BASE}robots.txt").respond(404)
    respx.get(f"{BASE}catalogue/page-1.html").respond(
        200, content=(FIXTURES / "books_list_page1.html").read_bytes(), headers=HEADERS
    )
    respx.get(url__regex=r".*/catalogue/[^/]+/index\.html$").respond(
        200, content=(FIXTURES / "books_detail.html").read_bytes(), headers=HEADERS
    )
    repo = repo_for(test_config)
    result = run_pipeline(test_config, repo, secrets=Secrets(_env_file=None))
    assert result.status == "success"
    assert result.mode == "demo"
    # Les 3 produits suivis figurent déjà en page 1 : pas de requête supplémentaire
    assert result.n_collected == 20
    assert result.n_valid == 20 and result.n_stored == 20
    assert set(result.reports) == {"html", "pdf"}
    assert all(p.is_file() for p in result.reports.values())
    runs = repo.get_runs()
    assert runs.iloc[0]["status"] == "success" and runs.iloc[0]["n_requests"] > 20


def test_pipeline_offline_products_and_alert_cooldown(
    test_config: AppConfig, listing_products: list
) -> None:
    repo = repo_for(test_config)
    repo.init_db()
    catalogue = demo.clean_products(listing_products, test_config.cleaning).data
    demo.seed_history(repo, test_config, days=40, catalogue=catalogue)

    first = run_pipeline(
        test_config,
        repo,
        products=listing_products,
        make_report=False,
        secrets=Secrets(_env_file=None),
    )
    assert first.status == "success"
    assert first.delivered_to == (["console"] if first.alerts else [])
    assert len(repo.get_alerts(limit=500)) == len(first.alerts)
    # Même jour, nouvelle exécution : les alertes déjà émises sont filtrées (cooldown)
    second = run_pipeline(
        test_config,
        repo,
        products=listing_products,
        make_report=False,
        send_alerts=False,
        secrets=Secrets(_env_file=None),
    )
    keys = {(a.product_id, a.kind) for a in first.alerts}
    assert not keys & {(a.product_id, a.kind) for a in second.alerts}
    assert second.delivered_to == []


def test_pipeline_partial_and_failed(test_config: AppConfig, listing_products: list) -> None:
    repo = repo_for(test_config)
    broken = [*listing_products[:3], dataclasses.replace(listing_products[3], price_raw="n/a")]
    partial = run_pipeline(test_config, repo, products=broken, make_report=False, send_alerts=False)
    assert partial.status == "partial" and partial.n_rejected == 1

    junk = [dataclasses.replace(p, price_raw="?") for p in listing_products[:2]]
    failed = run_pipeline(test_config, repo, products=junk, make_report=False)
    assert failed.status == "failed"
    assert repo.get_runs().iloc[0]["status"] == "failed"


def test_pipeline_exception_marks_run_failed(
    test_config: AppConfig, listing_products: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repo_for(test_config)

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("disque plein")

    monkeypatch.setattr(repo, "save_observations", boom)
    with pytest.raises(RuntimeError):
        run_pipeline(test_config, repo, products=listing_products, make_report=False)
    run = repo.get_runs().iloc[0]
    assert run["status"] == "failed" and "disque plein" in run["error"]


def test_live_mode_without_simulation(test_config: AppConfig, listing_products: list) -> None:
    test_config.sources[0].simulate_price_moves = False
    assert simulated_sources(test_config) == set()
    repo = repo_for(test_config)
    result = run_pipeline(test_config, repo, products=listing_products, make_report=False)
    assert result.mode == "live"
    prices = repo.get_price_history()
    assert prices.loc[prices["title"] == "A Light in the Attic", "price"].iloc[0] == 51.77


def test_demo_export_and_load(
    test_config: AppConfig, listing_products: list, tmp_path: Path
) -> None:
    repo = repo_for(test_config)
    catalogue = demo.clean_products(listing_products, test_config.cleaning).data
    catalogue.to_csv(tmp_path / demo.CATALOGUE_FILE, index=False)
    loaded = demo.load_catalogue(tmp_path)
    assert len(loaded) == 20 and str(loaded["collected_at"].dt.tz) == "UTC"
    assert demo.seed_history(repo, test_config, days=10, catalogue=loaded) == 200
    path = demo.export_history(repo, tmp_path)
    exported = pd.read_csv(path)
    assert len(exported) == 200 and "external_id" in exported

    other = PriceRepository.from_url(f"sqlite:///{tmp_path}/other.db")
    assert demo.load_history(other, path) == 200
    assert demo.load_history(other, path) == 0  # idempotent
