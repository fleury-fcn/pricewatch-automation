"""Chargement et validation de la configuration (YAML + variables d'environnement).

La configuration fonctionnelle (sources, seuils, planification) vit dans un fichier YAML
versionné ; les secrets (SMTP, tokens) sont lus depuis l'environnement ou un fichier ``.env``
et ne transitent jamais par le YAML.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config/config.yaml")

AlertChannel = Literal["console", "email", "telegram", "discord"]
AlertKind = Literal["price_drop", "target_reached", "anomaly"]


class _Strict(BaseModel):
    """Base : refuse les clés inconnues pour détecter les fautes de frappe dans le YAML."""

    model_config = ConfigDict(extra="forbid")


class StorageSettings(_Strict):
    database_url: str = "sqlite:///data/pricewatch.db"


class CollectionSettings(_Strict):
    user_agent: str = "PriceWatchBot/0.1 (+https://github.com/fleury-fcn/pricewatch-automation)"
    delay_seconds: float = Field(1.0, ge=0)
    timeout_seconds: float = Field(15.0, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    backoff_factor: float = Field(0.5, ge=0)
    respect_robots_txt: bool = True


class SourceConfig(_Strict):
    name: str
    connector: str
    enabled: bool = True
    base_url: str
    max_pages: int = Field(1, ge=1)
    categories: list[str] = Field(default_factory=list)
    simulate_price_moves: bool = False
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _ensure_trailing_slash(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url doit commencer par http:// ou https://")
        return value if value.endswith("/") else value + "/"


class WatchedProduct(_Strict):
    url: str
    name: str | None = None
    target_price: float | None = Field(None, gt=0)


class CleaningSettings(_Strict):
    default_currency: str = Field("GBP", pattern=r"^[A-Z]{3}$")
    base_currency: str = Field("EUR", pattern=r"^[A-Z]{3}$")
    # 1 unité de la devise = <taux> unités de base_currency (taux fixes indicatifs)
    exchange_rates: dict[str, float] = Field(
        default_factory=lambda: {"EUR": 1.0, "GBP": 1.15, "USD": 0.86, "CHF": 1.07}
    )
    max_price: float = Field(100_000, gt=0)

    @field_validator("exchange_rates")
    @classmethod
    def _positive_rates(cls, value: dict[str, float]) -> dict[str, float]:
        if any(rate <= 0 for rate in value.values()):
            raise ValueError("les taux de change doivent être strictement positifs")
        return {code.upper(): rate for code, rate in value.items()}


class AnalysisSettings(_Strict):
    significant_drop_pct: float = Field(10.0, gt=0, lt=100)
    zscore_threshold: float = Field(2.5, gt=0)
    trend_window_days: int = Field(14, ge=2)
    min_history_points: int = Field(5, ge=2)


class AlertSettings(_Strict):
    channels: list[AlertChannel] = Field(default_factory=lambda: ["console"])
    kinds: list[AlertKind] = Field(
        default_factory=lambda: ["price_drop", "target_reached", "anomaly"]
    )
    max_alerts_per_run: int = Field(20, ge=1)
    cooldown_hours: float = Field(72, ge=0)  # pas de nouvelle alerte identique avant ce délai


class ReportSettings(_Strict):
    output_dir: Path = Path("reports")
    title: str = "PriceWatch — Rapport de suivi des prix"
    top_n: int = Field(10, ge=1, le=100)
    formats: list[Literal["html", "pdf"]] = Field(default_factory=lambda: ["html", "pdf"])


class ScheduleSettings(_Strict):
    cron: str = "0 6 * * *"
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def _five_fields(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("l'expression cron doit contenir 5 champs")
        return value


class LoggingSettings(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file: Path | None = Path("logs/pricewatch.log")
    max_bytes: int = Field(1_048_576, ge=1024)
    backup_count: int = Field(5, ge=0)


class AppConfig(_Strict):
    """Configuration complète de l'application."""

    storage: StorageSettings = Field(default_factory=StorageSettings)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    sources: list[SourceConfig] = Field(default_factory=list)
    watchlist: list[WatchedProduct] = Field(default_factory=list)
    cleaning: CleaningSettings = Field(default_factory=CleaningSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    report: ReportSettings = Field(default_factory=ReportSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        """Sources actives uniquement."""
        return [source for source in self.sources if source.enabled]

    def target_prices(self) -> dict[str, float]:
        """Associe chaque URL suivie à son prix cible (si défini)."""
        return {w.url: w.target_price for w in self.watchlist if w.target_price is not None}


class Secrets(BaseSettings):
    """Secrets lus depuis l'environnement ou ``.env`` (jamais depuis le YAML)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    alert_email_from: str | None = None
    alert_email_to: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: SecretStr | None = None


def load_config(path: str | Path | None = None) -> AppConfig:
    """Charge et valide la configuration.

    Ordre de résolution du chemin : argument explicite, variable ``PRICEWATCH_CONFIG``,
    puis ``config/config.yaml``. La variable ``PRICEWATCH_DATABASE_URL`` surcharge l'URL
    de base de données (pratique en CI, Docker ou Streamlit Cloud).

    Args:
        path: Chemin du fichier YAML.

    Returns:
        La configuration validée.

    Raises:
        FileNotFoundError: Si le fichier n'existe pas.
        pydantic.ValidationError: Si le contenu est invalide.
    """
    config_path = Path(path or os.environ.get("PRICEWATCH_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        raise FileNotFoundError(f"Fichier de configuration introuvable : {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Le fichier de configuration doit contenir un objet YAML")
    config = AppConfig.model_validate(raw)
    if db_url := os.environ.get("PRICEWATCH_DATABASE_URL"):
        config.storage.database_url = db_url
    return config


def load_secrets() -> Secrets:
    """Charge les secrets depuis l'environnement."""
    return Secrets()
