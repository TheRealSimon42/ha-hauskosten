"""Pytest shared fixtures for hauskosten tests.

Der test-writer Agent erweitert diese Datei mit projekt-spezifischen Fixtures.
Siehe .claude/agents/test-writer.md fuer Konventionen.
"""

from __future__ import annotations

import pathlib
from datetime import date
from importlib.util import find_spec
from typing import Any

import pytest

# Enable pytest-homeassistant-custom-component auto-mode when the plugin is
# installed. Pure-logic tests (distribution.py, calculations.py) do not need
# it and run in leaner environments where HA is not available.
if find_spec("pytest_homeassistant_custom_component") is not None:
    pytest_plugins = ["pytest_homeassistant_custom_component"]

    # Editable pip installs (``pip install -e .``) inject a fake placeholder
    # path into ``custom_components.__path__`` (``__editable__...path_hook__``).
    # HA's ``_get_custom_components`` tries to iterate every entry of that
    # list and raises ``FileNotFoundError`` on the placeholder.  We strip any
    # non-existent path once per test session so the custom integration can
    # be discovered via the *real* on-disk location.
    import custom_components

    _real_paths = [
        p for p in list(custom_components.__path__) if pathlib.Path(p).is_dir()
    ]
    # ``__path__`` supports slice-assignment even for ``_NamespacePath``; using
    # that avoids replacing the container itself (HA relies on the live
    # ``_NamespacePath`` object to react to further finder insertions).
    custom_components.__path__[:] = _real_paths

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(
        enable_custom_integrations: None,
    ) -> None:
        """Enable loading of custom integrations in tests."""


@pytest.fixture
def sample_partei_og() -> dict[str, Any]:
    """Sample Partei: OG (Simon) — reference for most tests."""
    return {
        "id": "partei-og",
        "name": "OG (Simon)",
        "flaeche_qm": 85.0,
        "personen": 2,
        "bewohnt_ab": date(2020, 1, 1),
        "bewohnt_bis": None,
        "hinweis": None,
    }


@pytest.fixture
def sample_partei_dg() -> dict[str, Any]:
    """Sample Partei: DG (Mieter)."""
    return {
        "id": "partei-dg",
        "name": "DG (Mieter)",
        "flaeche_qm": 65.0,
        "personen": 1,
        "bewohnt_ab": date(2022, 6, 1),
        "bewohnt_bis": None,
        "hinweis": None,
    }


@pytest.fixture
def sample_kostenposition_versicherung() -> dict[str, Any]:
    """Sample: Gebaeudeversicherung 450 EUR/a, verteilt nach Flaeche."""
    return {
        "id": "kp-versicherung",
        "bezeichnung": "Gebäudeversicherung",
        "kategorie": "versicherung",
        "zuordnung": "haus",
        "zuordnung_partei_id": None,
        "betragsmodus": "pauschal",
        "betrag_eur": 450.0,
        "periodizitaet": "jaehrlich",
        "faelligkeit": date(2026, 3, 15),
        "verbrauchs_entity": None,
        "einheitspreis_eur": None,
        "einheit": None,
        "grundgebuehr_eur_monat": None,
        "verteilung": "flaeche",
        "verbrauch_entities_pro_partei": None,
        "aktiv_ab": None,
        "aktiv_bis": None,
        "notiz": None,
    }
