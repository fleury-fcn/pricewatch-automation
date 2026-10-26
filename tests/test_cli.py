from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pricewatch import __version__, demo
from pricewatch.cli import app
from pricewatch.config import AppConfig

runner = CliRunner()


@pytest.fixture
def cli_config(
    test_config: AppConfig, tmp_path: Path, listing_products: list, monkeypatch: pytest.MonkeyPatch
) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(test_config.model_dump(mode="json")), encoding="utf-8")
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    catalogue = demo.clean_products(listing_products, test_config.cleaning).data
    catalogue.to_csv(demo_dir / demo.CATALOGUE_FILE, index=False)
    monkeypatch.setattr(demo, "DEMO_DIR", demo_dir)
    monkeypatch.chdir(tmp_path)
    return path


def invoke(config: Path, *args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["--config", str(config), *args])


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and __version__ in result.output


def test_cli_end_to_end(
    cli_config: Path, tmp_path: Path, listing_products: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert invoke(cli_config, "init-db").exit_code == 0
    assert "Aucune exécution" in invoke(cli_config, "status").output

    result = invoke(cli_config, "seed-demo", "--days", "20")
    assert result.exit_code == 0, result.output
    assert "400 observations" in result.output

    from pricewatch.collectors import CollectionResult

    monkeypatch.setattr(
        "pricewatch.pipeline.collect_all",
        lambda config, http=None: CollectionResult(products=listing_products, requests=21),
    )
    result = invoke(cli_config, "run", "--no-alerts", "--max-pages", "1")
    assert result.exit_code == 0, result.output
    assert "success" in result.output and "rapport PDF" in result.output

    result = invoke(cli_config, "report", "--format", "html", "-o", str(tmp_path / "out"))
    assert result.exit_code == 0 and (tmp_path / "out" / "latest.html").is_file()

    status = invoke(cli_config, "status").output
    assert "Produits : 20" in status and "demo" in status

    result = invoke(cli_config, "export-demo")
    assert result.exit_code == 0 and (tmp_path / "demo" / demo.HISTORY_FILE).is_file()

    result = invoke(cli_config, "build-site", "--site-dir", str(tmp_path / "site"))
    assert result.exit_code == 0 and (tmp_path / "site" / "index.html").is_file()

    result = runner.invoke(app, ["--config", str(cli_config), "init-db", "--reset"], input="y\n")
    assert result.exit_code == 0
    assert "Produits : 0" in invoke(cli_config, "status").output


def test_run_failure_exit_code(cli_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pricewatch.collectors import CollectionResult

    monkeypatch.setattr(
        "pricewatch.pipeline.collect_all",
        lambda config, http=None: CollectionResult(errors=["réseau indisponible"]),
    )
    result = invoke(cli_config, "run", "--no-report")
    assert result.exit_code == 1 and "failed" in result.output
