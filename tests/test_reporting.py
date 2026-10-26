from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from pricewatch.alerts import build_alerts
from pricewatch.analysis import category_index, compute_product_stats
from pricewatch.config import AppConfig, load_config
from pricewatch.formatting import fmt_date, fmt_money, fmt_number, fmt_pct
from pricewatch.reporting import build_report_data, charts, executive_summary, generate_reports
from pricewatch.reporting.publish import _page_url, build_site
from pricewatch.reporting.report import _pdf_text, render_html, render_pdf
from pricewatch.simulation import generate_history
from pricewatch.storage import PriceRepository
from tests.factories import history_from_prices

NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


@pytest.fixture
def config(project_config_path: Path) -> AppConfig:
    return load_config(project_config_path)


@pytest.fixture
def filled_repo(tmp_path: Path, config: AppConfig) -> PriceRepository:
    catalogue = pd.DataFrame(
        {
            "source": "books_toscrape",
            "url": [f"https://books.toscrape.com/catalogue/b{i}/index.html" for i in range(12)],
            "title": [f"Livre <{i}> & co" for i in range(12)],
            "price": [10.0 + 3 * i for i in range(12)],
            "currency": "GBP",
            "price_base": None,
            "in_stock": True,
            "stock_qty": 4,
            "rating": 3,
            "category": ["Poetry", "Travel", "Fiction", "History", "Art", None] * 2,
            "external_id": None,
            "image_url": None,
            "collected_at": pd.Timestamp(NOW),
        }
    )
    repo = PriceRepository.from_url(f"sqlite:///{tmp_path}/r.db")
    repo.init_db()
    run_id = repo.start_run("demo")
    repo.save_observations(generate_history(catalogue, 60, end=date(2026, 10, 26)), run_id)
    stats = compute_product_stats(repo.get_price_history(), config.analysis)
    repo.save_alerts(build_alerts(stats, config.alerts), run_id, "console")
    repo.finish_run(run_id, "success")
    return repo


def test_formatting() -> None:
    assert fmt_number(1234.5) == "1 234,50"
    assert fmt_money(51.77, "GBP") == "51,77 £"
    assert fmt_money(3, "XYZ") == "3,00 XYZ"
    assert fmt_money(None, "EUR") == "—"
    assert fmt_pct(-12.34) == "−12,3 %"
    assert fmt_pct(5) == "+5,0 %"
    assert fmt_pct(0) == "0,0 %"
    assert fmt_pct(-3, signed=False) == "-3,0 %"
    assert fmt_pct(float("nan")) == "—"
    assert fmt_date(NOW) == "26/10/2026"
    assert fmt_date(pd.Timestamp(NOW), with_time=True) == "26/10/2026 07:00 UTC"
    assert fmt_date(None) == "—"


def test_pdf_text_sanitizes() -> None:
    assert _pdf_text("a < b & c") == "a &lt; b &amp; c"
    assert _pdf_text("−5 ≤ 3") == "-5 <= 3"
    assert _pdf_text("中") == "?"


def test_fold_categories_and_colors() -> None:
    history = pd.concat(
        [history_from_prices({i: [10.0, 11.0]}, category=f"C{i % 6}") for i in range(12)],
        ignore_index=True,
    )
    folded = charts.fold_categories(category_index(history), history, max_series=4)
    cats = list(folded["category"].cat.categories)
    assert len(cats) == 5 and cats[-1] == charts.OTHER_LABEL
    colors = charts.category_colors(cats)
    assert colors[charts.OTHER_LABEL] == charts.OTHER
    assert colors[cats[0]] == charts.SERIES[0]
    assert charts.fold_categories(pd.DataFrame(), history).empty


def test_report_data_and_summary(filled_repo: PriceRepository, config: AppConfig) -> None:
    data = build_report_data(filled_repo, config, generated_at=NOW)
    assert data.summary["n_products"] == 12
    assert data.simulated is True
    assert data.currency == "GBP"
    assert not data.drops.empty
    assert set(data.alerts["run_id"]) <= {filled_repo.get_runs().iloc[0]["id"]}
    assert data.highlights[0].startswith("12 produits suivis")
    assert "is_anomaly" in data.history


def test_empty_report(tmp_path: Path, config: AppConfig) -> None:
    repo = PriceRepository.from_url(f"sqlite:///{tmp_path}/e.db")
    repo.init_db()
    data = build_report_data(repo, config, generated_at=NOW)
    assert executive_summary(data) == [
        "Aucune donnée de prix disponible : lancez d'abord une collecte."
    ]
    assert data.currency == "EUR"
    assert "Aucune alerte" in render_html(data)
    assert render_pdf(data).startswith(b"%PDF")


def test_generate_reports(filled_repo: PriceRepository, config: AppConfig, tmp_path: Path) -> None:
    out = tmp_path / "reports"
    paths = generate_reports(filled_repo, config, out, generated_at=NOW)
    assert set(paths) == {"html_fr", "pdf_fr", "html_en", "pdf_en"}
    assert paths["html_fr"].name == "pricewatch_report_2026-10-26_fr.html"
    html = paths["html_fr"].read_text(encoding="utf-8")
    assert '<html lang="fr">' in html
    assert "Résumé exécutif" in html and "Mode démo" in html
    assert "cdn.plot.ly" in html and html.count("cdn.plot.ly") == 1
    assert "Livre &lt;" in html  # échappement Jinja2
    assert 'href="pricewatch_report_2026-10-26_en.html"' in html  # lien vers la version anglaise
    for key in ("pdf_fr", "pdf_en"):
        pdf = paths[key].read_bytes()
        assert pdf.startswith(b"%PDF") and len(pdf) > 20_000
    # latest.* = langue par défaut (la première configurée)
    assert (out / "latest.html").read_text(encoding="utf-8") == html
    assert (out / "latest_en.html").read_bytes() == paths["html_en"].read_bytes()
    with pytest.raises(ValueError):
        generate_reports(filled_repo, config, out, formats=["docx"])
    with pytest.raises(ValueError):
        generate_reports(filled_repo, config, out, languages=["de"])


def test_english_report(filled_repo: PriceRepository, config: AppConfig, tmp_path: Path) -> None:
    paths = generate_reports(
        filled_repo, config, tmp_path, formats=["html"], generated_at=NOW, languages=["en"]
    )
    assert set(paths) == {"html_en"}
    html = paths["html_en"].read_text(encoding="utf-8")
    assert '<html lang="en">' in html
    for text in (
        "Executive summary",
        "Demo mode.",
        "Top price drops",
        "Methodology",
        "Generated on 26 Oct 2026",
        "tracked products",
    ):
        assert text in html, text
    for french in ("Résumé exécutif", "Méthodologie", "Meilleures baisses", "produits suivis"):
        assert french not in html, french
    assert 'class="lang-switch"' not in html  # une seule langue : pas de lien
    data = build_report_data(filled_repo, config, generated_at=NOW, lang="en")
    assert data.title == "PriceWatch — Price Tracking Report"
    assert data.highlights[0].startswith("12 tracked products")
    categories = set(data.category_series["category"].astype(str))
    assert "Other" in categories and not categories & {"Autres", "Non classé"}
    if not data.alerts.empty:
        assert not any("baisse" in m or "« " in m for m in data.alerts["message"])
    assert render_pdf(data).startswith(b"%PDF")


def test_report_title_override(filled_repo: PriceRepository, config: AppConfig) -> None:
    config.report.title = "Veille tarifaire"
    for lang in ("fr", "en"):
        assert build_report_data(filled_repo, config, lang=lang).title == "Veille tarifaire"


def test_plotly_figures(filled_repo: PriceRepository, config: AppConfig) -> None:
    data = build_report_data(filled_repo, config, generated_at=NOW)
    fig = charts.px_price_history(data.history, [1, 2, 999])
    assert len(fig.data) == 2
    assert fig.data[0].line.color == charts.SERIES[0]
    assert len(charts.px_top_drops(data.drops).data) == 1
    assert charts.px_change_distribution(data.stats, 10).data[0].type == "histogram"


def test_build_site(filled_repo: PriceRepository, config: AppConfig, tmp_path: Path) -> None:
    reports, site = tmp_path / "reports", tmp_path / "site"
    generate_reports(filled_repo, config, reports, generated_at=NOW)
    (site / "archive").mkdir(parents=True)
    for day in ("2026-10-24", "2026-10-25"):  # anciens rapports sans suffixe de langue
        (site / "archive" / f"pricewatch_report_{day}.html").write_text("old")
    (site / "archive" / "pricewatch_report_2026-10-26.html").write_text("doublon")
    build_site(reports, site, keep_last=2)
    for name in ("index.html", "en/index.html", "latest.pdf", "latest_fr.pdf", "latest_en.pdf"):
        assert (site / name).is_file(), name
    assert (site / ".nojekyll").exists()
    home = (site / "index.html").read_text(encoding="utf-8")
    english = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert '<html lang="fr">' in home and 'href="./en/"' in home
    assert '<html lang="en">' in english and 'href="../"' in english
    archive = sorted(p.name for p in (site / "archive").iterdir())
    assert "pricewatch_report_2026-10-24.html" not in archive  # purge au-delà de keep_last
    assert "pricewatch_report_2026-10-26.html" not in archive  # doublon d'une version suffixée
    assert {
        f"pricewatch_report_2026-10-26_{lang}.{fmt}"
        for lang in ("fr", "en")
        for fmt in ("html", "pdf")
    } <= set(archive)
    index = (site / "archive" / "index.html").read_text(encoding="utf-8")
    assert "2026-10-26" in index and "2026-10-25" in index
    assert "HTML EN" in index and "PDF FR" in index
    with pytest.raises(FileNotFoundError):
        build_site(tmp_path / "nothing", site)


def test_build_site_single_language_legacy(tmp_path: Path) -> None:
    reports, site = tmp_path / "reports", tmp_path / "site"
    reports.mkdir()
    (reports / "latest.html").write_text("<html>rapport</html>", encoding="utf-8")
    build_site(reports, site)
    assert (site / "index.html").read_text(encoding="utf-8") == "<html>rapport</html>"
    assert not (site / "en").exists()


def test_site_language_urls() -> None:
    assert _page_url("fr", "fr", "fr") == "./"
    assert _page_url("en", "fr", "fr") == "./en/"
    assert _page_url("fr", "en", "fr") == "../"
