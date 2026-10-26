"""Assemblage du site statique publié sur GitHub Pages (derniers rapports FR/EN + archives)."""

from __future__ import annotations

import re
import shutil
from html import escape
from pathlib import Path

from pricewatch.i18n import DEFAULT_LANG, LANGUAGES
from pricewatch.logging_setup import get_logger

logger = get_logger(__name__)

# Rapports datés ; les anciens fichiers sans suffixe de langue restent reconnus.
_DATED = re.compile(r"pricewatch_report_(\d{4}-\d{2}-\d{2})(?:_([a-z]{2}))?\.(html|pdf)$")
_SWITCH = re.compile(r'(<a class="lang-switch" data-lang="([a-z]{2})"[^>]*? href=")[^"]*(")')
_LANG_NAMES = {"fr": "Français", "en": "English"}


def _page_url(target: str, current: str, default: str) -> str:
    """Lien relatif de la page d'accueil ``current`` vers celle de ``target``."""
    to_root = "./" if current == default else "../"
    return to_root if target == default else f"{to_root}{target}/"


def _rewrite_switches(html: str, current: str, default: str) -> str:
    """Fait pointer les liens de langue vers les pages d'accueil du site (``/`` et ``/en/``)."""
    return _SWITCH.sub(
        lambda m: f"{m.group(1)}{_page_url(m.group(2), current, default)}{m.group(3)}", html
    )


def build_site(
    reports_dir: Path,
    site_dir: Path,
    keep_last: int = 30,
    default_lang: str = DEFAULT_LANG,
) -> Path:
    """Copie les derniers rapports et les rapports datés dans ``site_dir``.

    Structure produite::

        site/index.html            dernier rapport HTML, langue par défaut (fr)
        site/en/index.html         dernier rapport HTML dans les autres langues
        site/latest.pdf            dernier rapport PDF, langue par défaut
        site/latest_<lang>.pdf     dernier rapport PDF par langue
        site/archive/index.html    liste des rapports datés
        site/archive/<rapports datés>

    Les archives déjà présentes dans ``site_dir`` (déploiements précédents) sont
    conservées, dans la limite des ``keep_last`` jours les plus récents.

    Returns:
        Le dossier du site.
    """
    pages: dict[str, Path] = {
        lang: reports_dir / f"latest_{lang}.html"
        for lang in LANGUAGES
        if (reports_dir / f"latest_{lang}.html").is_file()
    }
    if not pages and (reports_dir / "latest.html").is_file():  # rapport monolingue
        pages = {default_lang: reports_dir / "latest.html"}
    if not pages:
        raise FileNotFoundError(
            f"Aucun rapport HTML dans {reports_dir} : lancez `pricewatch report`."
        )
    default = default_lang if default_lang in pages else next(iter(pages))

    archive = site_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    for lang, source in pages.items():
        target = site_dir / "index.html" if lang == default else site_dir / lang / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        html = source.read_text(encoding="utf-8")
        target.write_text(_rewrite_switches(html, lang, default), encoding="utf-8")
    for path in reports_dir.glob("latest*.pdf"):
        shutil.copyfile(path, site_dir / path.name)
    for path in reports_dir.iterdir():
        if _DATED.match(path.name):
            shutil.copyfile(path, archive / path.name)
    (site_dir / ".nojekyll").touch()

    dated: dict[str, dict[tuple[str, str], str]] = {}
    legacy: list[Path] = []

    def suffixed_first(path: Path) -> bool:
        match = _DATED.match(path.name)
        return match is None or match.group(2) is None  # False (suffixé) trié en premier

    for path in sorted(archive.iterdir(), key=suffixed_first):
        if match := _DATED.match(path.name):
            day, file_lang, fmt = match.group(1), match.group(2) or default, match.group(3)
            entries = dated.setdefault(day, {})
            if match.group(2) is None and (file_lang, fmt) in entries:
                legacy.append(path)  # doublon d'une version suffixée : supprimé
                continue
            entries[(file_lang, fmt)] = path.name
    for path in legacy:
        path.unlink()
    days = sorted(dated, reverse=True)
    for old_day in days[keep_last:]:
        for name in dated[old_day].values():
            (archive / name).unlink()
    rows = "\n".join(
        "<li>{day} — {links}</li>".format(
            day=escape(day),
            links=" · ".join(
                f'<a href="{escape(name)}">{fmt.upper()} {lang.upper()}</a>'
                for (lang, fmt), name in sorted(dated[day].items())
            ),
        )
        for day in days[:keep_last]
    )
    home_links = " · ".join(
        f"<a href='{'../' if lang == default else f'../{lang}/'}'>{_LANG_NAMES.get(lang, lang)}</a>"
        for lang in pages
    )
    (archive / "index.html").write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>PriceWatch — Archives</title>"
        "<style>body{font:15px/1.6 system-ui,sans-serif;max-width:720px;margin:40px auto;"
        "padding:0 16px;color:#0b0b0b;background:#f9f9f7}a{color:#2a78d6}</style></head><body>"
        "<h1>Archives des rapports · Report archive</h1>"
        f"<p>← {home_links}</p>"
        f"<ul>{rows}</ul></body></html>",
        encoding="utf-8",
    )
    logger.info("Site statique prêt : %s (%d jours archivés)", site_dir, len(days[:keep_last]))
    return site_dir
