import logging
from pathlib import Path

from pricewatch.config import LoggingSettings
from pricewatch.logging_setup import _HANDLER_TAG, get_logger, setup_logging


def own_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, _HANDLER_TAG, False)]


def test_setup_logging_is_idempotent_and_rotates(tmp_path: Path) -> None:
    settings = LoggingSettings(file=tmp_path / "logs" / "app.log", max_bytes=1024, backup_count=2)
    logger = setup_logging(settings)
    setup_logging(settings)
    assert len(own_handlers(logger)) == 2
    for i in range(200):
        get_logger("test").info("ligne de log numéro %d avec un peu de texte", i)
    files = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert files == ["app.log", "app.log.1", "app.log.2"]
    setup_logging(LoggingSettings(file=None))
    assert len(own_handlers(logger)) == 1


def test_get_logger_namespacing() -> None:
    assert get_logger("x").name == "pricewatch.x"
    assert get_logger("pricewatch.y").name == "pricewatch.y"
    assert isinstance(get_logger("pricewatch"), logging.Logger)
