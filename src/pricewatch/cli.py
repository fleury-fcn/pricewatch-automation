"""Interface en ligne de commande : ``pricewatch --help``."""

from __future__ import annotations

import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from pricewatch import __version__
from pricewatch.config import AppConfig, load_config
from pricewatch.logging_setup import setup_logging
from pricewatch.storage import PriceRepository

app = typer.Typer(
    name="pricewatch",
    help="PriceWatch — suivi automatisé de prix e-commerce.",
    no_args_is_help=True,
    add_completion=False,
)


class ReportFormat(StrEnum):
    """Formats de rapport disponibles."""

    html = "html"
    pdf = "pdf"


class _State:
    config_path: Path | None = None


state = _State()


def _config() -> AppConfig:
    config = load_config(state.config_path)
    setup_logging(config.logging)
    return config


def _repo(config: AppConfig) -> PriceRepository:
    repo = PriceRepository.from_url(config.storage.database_url)
    repo.init_db()
    return repo


def _version(value: bool) -> None:
    if value:
        typer.echo(f"pricewatch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Fichier YAML (défaut : config/config.yaml).",
            envvar="PRICEWATCH_CONFIG",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Affiche la version."),
    ] = False,
) -> None:
    """Options globales."""
    state.config_path = config


@app.command("init-db")
def init_db(
    reset: Annotated[bool, typer.Option("--reset", help="Supprime et recrée les tables.")] = False,
) -> None:
    """Crée les tables de la base de données (idempotent)."""
    config = _config()
    repo = PriceRepository.from_url(config.storage.database_url)
    if reset:
        typer.confirm("Toutes les données seront supprimées. Continuer ?", abort=True)
        repo.reset_db()
    else:
        repo.init_db()
    typer.secho(f"Base prête : {config.storage.database_url}", fg=typer.colors.GREEN)


@app.command()
def run(
    no_alerts: Annotated[
        bool, typer.Option("--no-alerts", help="N'envoie pas les alertes.")
    ] = False,
    no_report: Annotated[
        bool, typer.Option("--no-report", help="Ne génère pas le rapport.")
    ] = False,
    max_pages: Annotated[
        int | None, typer.Option("--max-pages", min=1, help="Limite de pages par source.")
    ] = None,
) -> None:
    """Exécute le pipeline complet : collecte, nettoyage, stockage, analyse, alertes, rapport."""
    from pricewatch.pipeline import run_pipeline

    config = _config()
    if max_pages is not None:
        for source in config.sources:
            source.max_pages = max_pages
    result = run_pipeline(
        config, _repo(config), send_alerts=not no_alerts, make_report=not no_report
    )
    color = {"success": typer.colors.GREEN, "partial": typer.colors.YELLOW}.get(
        result.status, typer.colors.RED
    )
    typer.secho(f"Exécution #{result.run_id} : {result.status} ({result.duration_s} s)", fg=color)
    typer.echo(
        f"  collectés {result.n_collected} · valides {result.n_valid} · rejetés {result.n_rejected}"
        f" · nouvelles observations {result.n_stored} · alertes {len(result.alerts)}"
    )
    for fmt, path in result.reports.items():
        typer.echo(f"  rapport {fmt.upper()} : {path}")
    if result.status == "failed":
        raise typer.Exit(code=1)


@app.command()
def report(
    formats: Annotated[
        list[ReportFormat] | None, typer.Option("--format", "-f", help="Format(s) à produire.")
    ] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o")] = None,
) -> None:
    """Génère le rapport HTML/PDF à partir des données déjà en base."""
    from pricewatch.reporting import generate_reports

    config = _config()
    fmts = [f.value for f in formats] if formats else None
    for fmt, path in generate_reports(_repo(config), config, output_dir, fmts).items():
        typer.secho(f"Rapport {fmt.upper()} : {path}", fg=typer.colors.GREEN)


@app.command()
def status(limit: Annotated[int, typer.Option(min=1)] = 5) -> None:
    """Affiche l'état de la base et les dernières exécutions."""
    config = _config()
    repo = _repo(config)
    products = repo.get_products()
    typer.echo(f"Base : {config.storage.database_url}")
    typer.echo(f"Produits : {len(products)} · observations : {repo.count_observations()}")
    runs = repo.get_runs(limit)
    if runs.empty:
        typer.echo("Aucune exécution enregistrée.")
        return
    for r in runs.itertuples():
        typer.echo(
            f"  #{r.id} {r.started_at:%Y-%m-%d %H:%M} {r.mode:<5} {r.status:<8} "
            f"valides={r.n_valid} stockés={r.n_stored} alertes={r.n_alerts}"
        )


@app.command("seed-demo")
def seed_demo(
    days: Annotated[int, typer.Option(min=2, max=730, help="Jours d'historique simulé.")] = 90,
    refresh_catalogue: Annotated[
        bool, typer.Option("--refresh-catalogue", help="Recollecte le catalogue réel en ligne.")
    ] = False,
) -> None:
    """Remplit la base avec un historique simulé (mode démo) à partir du catalogue réel."""
    from pricewatch import demo

    config = _config()
    catalogue = demo.build_catalogue(config) if refresh_catalogue else demo.load_catalogue()
    inserted = demo.seed_history(_repo(config), config, days, catalogue)
    typer.secho(
        f"{inserted} observations simulées insérées ({len(catalogue)} produits).",
        fg=typer.colors.GREEN,
    )


@app.command("export-demo")
def export_demo(
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o")] = None,
) -> None:
    """Exporte l'historique en CSV (jeu de données embarqué du dashboard)."""
    from pricewatch import demo

    path = demo.export_history(_repo(_config()), output_dir)
    typer.secho(f"Historique exporté : {path}", fg=typer.colors.GREEN)


@app.command("build-site")
def build_site(
    site_dir: Annotated[Path, typer.Option("--site-dir")] = Path("site"),
    keep_last: Annotated[int, typer.Option(min=1)] = 30,
) -> None:
    """Prépare le site statique GitHub Pages (dernier rapport + archives)."""
    from pricewatch.reporting.publish import build_site as _build

    config = _config()
    _build(Path(config.report.output_dir), site_dir, keep_last)
    typer.secho(f"Site prêt : {site_dir}", fg=typer.colors.GREEN)


@app.command()
def api(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:  # pragma: no cover - lance un serveur bloquant
    """Démarre l'API REST (documentation Swagger sur /docs)."""
    import uvicorn

    uvicorn.run("pricewatch.api:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def dashboard(port: Annotated[int, typer.Option()] = 8501) -> None:  # pragma: no cover
    """Démarre le dashboard Streamlit."""
    script = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
    raise typer.Exit(
        subprocess.call(
            [sys.executable, "-m", "streamlit", "run", str(script), "--server.port", str(port)]
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
