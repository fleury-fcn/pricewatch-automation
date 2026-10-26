"""Configuration centralisée des logs (console + fichier avec rotation)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from pricewatch.config import LoggingSettings

LOGGER_NAME = "pricewatch"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_HANDLER_TAG = "_pricewatch_handler"


def setup_logging(settings: LoggingSettings | None = None) -> logging.Logger:
    """Configure le logger ``pricewatch``.

    La fonction est idempotente : un nouvel appel remplace les handlers posés par un
    appel précédent au lieu de les dupliquer.

    Args:
        settings: Paramètres de log ; valeurs par défaut si ``None``.

    Returns:
        Le logger racine de l'application.
    """
    settings = settings or LoggingSettings()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(settings.level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.file is not None:
        settings.file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                settings.file,
                maxBytes=settings.max_bytes,
                backupCount=settings.backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_TAG, True)
        logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger enfant de ``pricewatch`` (ex. ``pricewatch.collectors``)."""
    if name == LOGGER_NAME or name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
