from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import inspect

from pricewatch.storage import AlertRecord, PriceRepository
from pricewatch.storage.models import UTCDateTime

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def observation(url: str = "https://a.test/1", price: float = 10.0, day: int = 0, **extra: object):
    return {
        "source": "books",
        "url": url,
        "title": extra.pop("title", "Livre"),
        "price": price,
        "currency": "GBP",
        "price_base": round(price * 1.15, 2),
        "in_stock": True,
        "stock_qty": 5,
        "rating": 4,
        "category": extra.pop("category", "Poetry"),
        "external_id": "upc1",
        "image_url": None,
        "collected_at": pd.Timestamp(T0 + timedelta(days=day)),
        **extra,
    }


@pytest.fixture
def repo(tmp_path: Path) -> PriceRepository:
    repository = PriceRepository.from_url(f"sqlite:///{tmp_path}/sub/test.db")
    repository.init_db()
    return repository


def test_init_creates_tables_and_indexes(repo: PriceRepository) -> None:
    inspector = inspect(repo.engine)
    assert {"products", "price_history", "runs", "alerts"} <= set(inspector.get_table_names())
    index_names = {i["name"] for i in inspector.get_indexes("price_history")}
    assert "ix_price_product_time" in index_names
    repo.init_db()  # idempotent


def test_save_observations_is_idempotent(repo: PriceRepository) -> None:
    run_id = repo.start_run()
    df = pd.DataFrame([observation(day=0), observation(day=1, price=9.0)])
    assert repo.save_observations(df, run_id) == 2
    assert repo.save_observations(df, run_id) == 0
    assert repo.count_observations() == 2
    assert repo.save_observations(pd.DataFrame()) == 0


def test_product_metadata_follows_latest_observation(repo: PriceRepository) -> None:
    repo.save_observations(pd.DataFrame([observation(day=5, title="Nouveau titre")]))
    # backfill plus ancien : ne doit pas écraser le titre mais avancer first_seen
    repo.save_observations(pd.DataFrame([observation(day=0, title="Ancien titre")]))
    repo.save_observations(pd.DataFrame([observation(day=3, title="Intermédiaire")]))
    products = repo.get_products()
    assert len(products) == 1
    row = products.iloc[0]
    assert row["title"] == "Nouveau titre"
    assert row["first_seen"] == T0
    assert row["last_seen"] == T0 + timedelta(days=5)
    assert row["n_observations"] == 3
    repo.save_observations(pd.DataFrame([observation(day=9, title="Dernier", category=None)]))
    row = repo.get_products().iloc[0]
    assert row["title"] == "Dernier" and row["category"] == "Poetry"
    assert row["last_price"] == 10.0


def test_history_filters(repo: PriceRepository) -> None:
    rows = [observation(day=d, price=10 + d) for d in range(5)]
    rows += [observation(url="https://a.test/2", day=d, category="Travel") for d in range(3)]
    repo.save_observations(pd.DataFrame(rows))
    history = repo.get_price_history()
    assert len(history) == 8
    assert history["collected_at"].is_monotonic_increasing
    assert str(history["collected_at"].dt.tz) == "UTC"
    pid = int(history.loc[history["url"] == "https://a.test/1", "product_id"].iloc[0])
    assert len(repo.get_price_history(product_ids=[pid])) == 5
    assert len(repo.get_price_history(since=T0 + timedelta(days=3))) == 2
    assert len(repo.get_price_history(category="Travel")) == 3
    latest = repo.get_price_history(limit=2)
    assert list(latest["collected_at"]) == [T0 + timedelta(days=3), T0 + timedelta(days=4)]


def test_empty_history(repo: PriceRepository) -> None:
    assert repo.get_price_history().empty
    assert repo.get_products().empty
    assert repo.get_alerts().empty
    assert repo.get_runs().empty


def test_runs_lifecycle(repo: PriceRepository) -> None:
    run_id = repo.start_run(mode="demo")
    repo.finish_run(run_id, "success", n_valid=3, n_alerts=1)
    runs = repo.get_runs()
    assert runs.iloc[0]["status"] == "success"
    assert runs.iloc[0]["mode"] == "demo"
    assert runs.iloc[0]["n_valid"] == 3
    assert runs.iloc[0]["finished_at"] >= runs.iloc[0]["started_at"]
    with pytest.raises(KeyError):
        repo.finish_run(999, "failed")
    with pytest.raises(AttributeError):
        repo.finish_run(run_id, "failed", unknown=1)


def test_alerts_roundtrip(repo: PriceRepository) -> None:
    repo.save_observations(pd.DataFrame([observation()]))
    pid = int(repo.get_products().iloc[0]["id"])
    run_id = repo.start_run()
    saved = repo.save_alerts(
        [
            AlertRecord(pid, "price_drop", "Baisse de 20 %", 8.0, 10.0, -20.0),
            AlertRecord(pid, "anomaly", "Prix anormal", 30.0),
        ],
        run_id,
        delivered_to="console",
    )
    assert saved == 2
    alerts = repo.get_alerts()
    assert set(alerts["kind"]) == {"price_drop", "anomaly"}
    assert alerts.iloc[0]["title"] == "Livre"
    assert len(repo.get_alerts(kind="anomaly")) == 1
    assert repo.get_alerts(since=datetime.now(UTC) + timedelta(days=1)).empty


def test_reset_db(repo: PriceRepository) -> None:
    repo.save_observations(pd.DataFrame([observation()]))
    repo.reset_db()
    assert repo.count_observations() == 0


def test_foreign_keys_enforced(repo: PriceRepository) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        repo.save_alerts([AlertRecord(12345, "anomaly", "x", 1.0)], None)


def test_utc_datetime_type() -> None:
    t = UTCDateTime()
    assert t.process_bind_param(None, None) is None  # type: ignore[arg-type]
    assert t.process_result_value(None, None) is None  # type: ignore[arg-type]
    naive = datetime(2026, 1, 1, 12)
    assert t.process_bind_param(naive, None) == naive  # type: ignore[arg-type]
    paris = datetime(2026, 1, 1, 13, tzinfo=__import__("zoneinfo").ZoneInfo("Europe/Paris"))
    assert t.process_bind_param(paris, None) == naive  # type: ignore[arg-type]
    assert t.process_result_value(paris, None) == naive.replace(tzinfo=UTC)  # type: ignore[arg-type]


def test_memory_database() -> None:
    repo = PriceRepository.from_url("sqlite:///:memory:")
    repo.init_db()
    assert repo.count_observations() == 0
