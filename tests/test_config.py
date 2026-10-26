from pathlib import Path

import pytest
from pydantic import ValidationError

from pricewatch.config import AppConfig, load_config, load_secrets


def test_project_config_is_valid(project_config_path: Path) -> None:
    config = load_config(project_config_path)
    assert config.enabled_sources, "au moins une source active attendue"
    assert config.sources[0].base_url.endswith("/")
    assert config.analysis.zscore_threshold > 0
    assert all(price > 0 for price in config.target_prices().values())


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("analysis:\n  zscore_treshold: 3\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_invalid_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"analysis": {"significant_drop_pct": -5}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"schedule": {"cron": "* * *"}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"sources": [{"name": "x", "connector": "x", "base_url": "ftp://x"}]}
        )


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("sources: []\n", encoding="utf-8")
    monkeypatch.setenv("PRICEWATCH_CONFIG", str(path))
    monkeypatch.setenv("PRICEWATCH_DATABASE_URL", "sqlite:///:memory:")
    config = load_config()
    assert config.storage.database_url == "sqlite:///:memory:"
    assert config.enabled_sources == []


def test_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("SMTP_PORT", "2525")
    secrets = load_secrets()
    assert secrets.telegram_bot_token is not None
    assert secrets.telegram_bot_token.get_secret_value() == "abc"
    assert "abc" not in repr(secrets)
    assert secrets.smtp_port == 2525
