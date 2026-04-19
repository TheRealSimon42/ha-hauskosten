"""Pytest shared fixtures for hauskosten tests.

Der test-writer Agent erweitert diese Datei mit projekt-spezifischen Fixtures.
Siehe .claude/agents/test-writer.md fuer Konventionen.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

# Enable pytest-homeassistant-custom-component auto-mode
pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,  # noqa: ARG001  # pytest DI
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
