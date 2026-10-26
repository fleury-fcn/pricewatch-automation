from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pricewatch import demo
from pricewatch.api import create_app
from pricewatch.config import AppConfig
from pricewatch.pipeline import run_pipeline
from pricewatch.storage import PriceRepository


@pytest.fixture
def repo(test_config: AppConfig, listing_products: list) -> PriceRepository:
    repository = PriceRepository.from_url(test_config.storage.database_url)
    repository.init_db()
    catalogue = demo.clean_products(listing_products, test_config.cleaning).data
    demo.seed_history(repository, test_config, days=30, catalogue=catalogue)
    run_pipeline(test_config, repository, products=listing_products, send_alerts=False)
    return repository


@pytest.fixture
def client(test_config: AppConfig, repo: PriceRepository) -> TestClient:
    calls: list[dict[str, Any]] = []

    def fake_runner(config: AppConfig, r: PriceRepository, **kwargs: Any) -> None:
        calls.append({"max_pages": config.sources[0].max_pages, **kwargs})

    app = create_app(test_config, repo, pipeline_runner=fake_runner)
    app.state.calls = calls
    with TestClient(app) as c:
        yield c


def test_docs_and_health(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/docs"
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert {"/prices", "/alerts", "/run", "/stats"} <= set(schema["paths"])
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["products"] == 20
    assert health["observations"] == 620


def test_products_and_prices(client: TestClient) -> None:
    products = client.get("/products").json()
    assert len(products) == 20
    pid = products[0]["id"]
    prices = client.get("/prices", params={"product_id": pid}).json()
    assert len(prices) == 31
    assert prices[0]["collected_at"] < prices[-1]["collected_at"]
    assert len(client.get("/prices", params={"limit": 5}).json()) == 5
    assert client.get("/prices", params={"limit": 0}).status_code == 422
    assert client.get("/products", params={"category": "nope"}).json() == []


def test_stats_and_alerts(client: TestClient) -> None:
    stats = client.get("/stats").json()
    assert len(stats) == 20 and {"zscore", "trend", "drop_pct"} <= set(stats[0])
    drops = client.get("/stats", params={"only": "drops"}).json()
    assert all(s["is_significant_drop"] for s in drops)
    alerts = client.get("/alerts").json()
    assert all(a["kind"] in {"price_drop", "target_reached", "anomaly"} for a in alerts)
    assert client.get("/alerts", params={"kind": "bogus"}).status_code == 422
    runs = client.get("/runs").json()
    assert runs[0]["status"] == "success"


def test_trigger_run(client: TestClient) -> None:
    response = client.post("/run", json={"send_alerts": False, "max_pages": 2})
    assert response.status_code == 202
    assert client.app.state.calls == [{"max_pages": 2, "send_alerts": False, "make_report": True}]
    assert client.post("/run").status_code == 202  # verrou libéré après la tâche


def test_run_conflict_and_api_key(
    test_config: AppConfig, repo: PriceRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRICEWATCH_API_KEY", "s3cret")

    def failing_runner(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("panne")

    app = create_app(test_config, repo, pipeline_runner=failing_runner)
    with TestClient(app) as c:
        assert c.post("/run").status_code == 401
        assert c.post("/run", headers={"X-API-Key": "bad"}).status_code == 401
        assert c.post("/run", headers={"X-API-Key": "s3cret"}).status_code == 202
        assert c.get("/health").json()["pipeline_running"] is False  # libéré malgré l'erreur
        # verrou déjà pris par une autre exécution -> 409
        guard = c.app.state.guard
        assert guard.try_acquire()
        assert c.post("/run", headers={"X-API-Key": "s3cret"}).status_code == 409
        assert c.get("/health").json()["pipeline_running"] is True
        guard.release()


def test_reports_download(client: TestClient, test_config: AppConfig) -> None:
    pdf = client.get("/reports/latest.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/reports/latest.html").headers["content-type"].startswith("text/html")
    assert client.get("/reports/latest.docx").status_code == 422
    english = client.get("/reports/latest.html", params={"lang": "en"})
    assert english.status_code == 200 and "Executive summary" in english.text
    assert "Résumé exécutif" in client.get("/reports/latest.html").text  # défaut : fr
    assert client.get("/reports/latest.pdf", params={"lang": "de"}).status_code == 422
    (Path(test_config.report.output_dir) / "latest_fr.pdf").unlink()
    assert client.get("/reports/latest.pdf").status_code == 404
    assert client.get("/reports/latest.pdf", params={"lang": "en"}).status_code == 200
