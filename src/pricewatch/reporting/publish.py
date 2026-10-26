"""Assemblage du site statique publié sur GitHub Pages (dernier rapport + archives)."""

from __future__ import annotations

import re
import shutil
from html import escape
from pathlib import Path

from pricewatch.logging_setup import get_logger

logger = get_logger(__name__)

_DATED = re.compile(r"pricewatch_report_(\d{4}-\d{2}-\d{2})\.(html|pdf)$")


def build_site(reports_dir: Path, site_dir: Path, keep_last: int = 30) -> Path:
    """Copie le dernier rapport et les rapports datés dans ``site_dir``.

    Structure produite::

        site/index.html            dernier rapport HTML
        site/latest.pdf            dernier rapport PDF
        site/archive/index.html    liste des rapports datés
        site/archive/<rapports datés>

    Les archives déjà présentes dans ``site_dir`` (déploiements précédents) sont
    conservées, dans la limite des ``keep_last`` jours les plus récents.

    Returns:
        Le dossier du site.
    """
    latest_html = reports_dir / "latest.html"
    if not latest_html.is_file():
        raise FileNotFoundError(
            f"Aucun rapport HTML dans {reports_dir} : lancez `pricewatch report`."
        )
    archive = site_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(latest_html, site_dir / "index.html")
    if (reports_dir / "latest.pdf").is_file():
        shutil.copyfile(reports_dir / "latest.pdf", site_dir / "latest.pdf")
    for path in reports_dir.iterdir():
        if _DATED.match(path.name):
            shutil.copyfile(path, archive / path.name)
    (site_dir / ".nojekyll").touch()

    dated: dict[str, dict[str, str]] = {}
    for path in archive.iterdir():
        if match := _DATED.match(path.name):
            dated.setdefault(match.group(1), {})[match.group(2)] = path.name
    days = sorted(dated, reverse=True)
    for old_day in days[keep_last:]:
        for name in dated[old_day].values():
            (archive / name).unlink()
    rows = "\n".join(
        "<li>{day} — {links}</li>".format(
            day=escape(day),
            links=" · ".join(
                f'<a href="{escape(name)}">{fmt.upper()}</a>'
                for fmt, name in sorted(dated[day].items())
            ),
        )
        for day in days[:keep_last]
    )
    (archive / "index.html").write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>PriceWatch — Archives</title>"
        "<style>body{font:15px/1.6 system-ui,sans-serif;max-width:720px;margin:40px auto;"
        "padding:0 16px;color:#0b0b0b;background:#f9f9f7}a{color:#2a78d6}</style></head><body>"
        "<h1>Archives des rapports</h1><p><a href='../'>← Dernier rapport</a></p>"
        f"<ul>{rows}</ul></body></html>",
        encoding="utf-8",
    )
    logger.info("Site statique prêt : %s (%d rapports archivés)", site_dir, len(days[:keep_last]))
    return site_dir
