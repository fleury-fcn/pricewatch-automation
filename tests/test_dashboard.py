"""Tests du dashboard Streamlit (exécution réelle du script via AppTest)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("PRICEWATCH_MODE", "demo")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def test_demo_dashboard_renders(app: AppTest) -> None:
    assert app.title[0].value == "Suivi des prix e-commerce"
    assert "Mode démo" in app.sidebar.info[0].value
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Produits suivis"] == "60"
    assert len(app.tabs) == 5
    assert len(app.get("plotly_chart")) >= 4


def test_category_filter(app: AppTest) -> None:
    app.sidebar.multiselect[0].set_value(["Poetry"]).run()
    assert not app.exception
    assert int({m.label: m.value for m in app.metric}["Produits suivis"]) < 60


def test_offline_pipeline_run_and_report(app: AppTest) -> None:
    app.sidebar.toggle[0].set_value(False).run()  # pas de réseau : catalogue embarqué
    app.sidebar.button[0].click().run()
    assert not app.exception, app.exception
    runs = next(df.value for df in app.dataframe if "n_alerts" in df.value.columns)
    assert len(runs) == 2  # exécution « démo » initiale + celle du bouton
    assert runs.iloc[0]["status"] == "success" and runs.iloc[0]["n_valid"] == 60

    next(b for b in app.button if b.label == "Générer le rapport").click().run()
    assert not app.exception, app.exception
    assert app.session_state["report_paths"]["pdf"].is_file()
