"""Fixtures partagées."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project_config_path() -> Path:
    """Chemin du fichier de configuration du projet."""
    return ROOT / "config" / "config.yaml"
