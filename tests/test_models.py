"""Tests for custom_components.hauskosten.models.

models.py is a type-only module: the TypedDicts carry no runtime shape
behaviour but the StrEnums do. These tests lock the enum *values* (which are
persisted strings, part of the schema contract) and smoke-test that the
TypedDicts accept the fields documented in docs/DATA_MODEL.md.

Authoritative oracle: docs/DATA_MODEL.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import get_type_hints

import pytest

from custom_components.hauskosten.models import (
    AdHocKosten,
    Betragsmodus,
    CoordinatorData,
    Einheit,
    HausResult,
    Kategorie,
    Kostenposition,
    Partei,
    ParteiResult,
    Periodizitaet,
    PositionAttribution,
    Verteilung,
    Zuordnung,
)

# ---------------------------------------------------------------------------
# Enum value contracts (these strings are persisted in subentries / store)
# ---------------------------------------------------------------------------


class TestKategorie:
    """Locked values for Kategorie -- renames are breaking schema changes."""

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (Kategorie.VERSICHERUNG, "versicherung"),
            (Kategorie.MUELL, "muell"),
            (Kategorie.WASSER, "wasser"),
            (Kategorie.ABWASSER, "abwasser"),
            (Kategorie.STROM, "strom"),
            (Kategorie.HEIZUNG, "heizung"),
            (Kategorie.WARTUNG, "wartung"),
            (Kategorie.GRUND, "grund"),
            (Kategorie.HAUSGELD, "hausgeld"),
            (Kategorie.KOMMUNIKATION, "kommunikation"),
            (Kategorie.REINIGUNG, "reinigung"),
            (Kategorie.SONSTIGES, "sonstiges"),
        ],
    )
    def test_value(self, member: Kategorie, expected: str) -> None:
        assert member.value == expected
        # StrEnum instances compare equal to their raw string value.
        assert member == expected

    def test_twelve_members(self) -> None:
        assert len(Kategorie) == 12

    def test_roundtrip_from_string(self) -> None:
        assert Kategorie("wasser") is Kategorie.WASSER


class TestZuordnung:
    def test_values(self) -> None:
        assert Zuordnung.HAUS.value == "haus"
        assert Zuordnung.PARTEI.value == "partei"

    def test_two_members(self) -> None:
        assert len(Zuordnung) == 2


class TestBetragsmodus:
    def test_values(self) -> None:
        assert Betragsmodus.PAUSCHAL.value == "pauschal"
        assert Betragsmodus.VERBRAUCH.value == "verbrauch"
        assert len(Betragsmodus) == 2


class TestPeriodizitaet:
    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (Periodizitaet.MONATLICH, "monatlich"),
            (Periodizitaet.QUARTALSWEISE, "quartalsweise"),
            (Periodizitaet.HALBJAEHRLICH, "halbjaehrlich"),
            (Periodizitaet.JAEHRLICH, "jaehrlich"),
            (Periodizitaet.EINMALIG, "einmalig"),
        ],
    )
    def test_value(self, member: Periodizitaet, expected: str) -> None:
        assert member.value == expected

    def test_five_members(self) -> None:
        assert len(Periodizitaet) == 5


class TestEinheit:
    def test_values(self) -> None:
        # Value is the short spec form, not the long Python member name.
        assert Einheit.KUBIKMETER.value == "m3"
        assert Einheit.KWH.value == "kwh"
        assert Einheit.LITER.value == "liter"
        assert len(Einheit) == 3


class TestVerteilung:
    def test_values(self) -> None:
        assert Verteilung.DIREKT.value == "direkt"
        assert Verteilung.GLEICH.value == "gleich"
        assert Verteilung.FLAECHE.value == "flaeche"
        assert Verteilung.PERSONEN.value == "personen"
        # Intentional: the spec uses "verbrauch" for the subzaehler key.
        assert Verteilung.VERBRAUCH_SUBZAEHLER.value == "verbrauch"
        assert len(Verteilung) == 5

    def test_subzaehler_string_equality(self) -> None:
        assert Verteilung.VERBRAUCH_SUBZAEHLER == "verbrauch"


# ---------------------------------------------------------------------------
# TypedDict shape smoke tests
# ---------------------------------------------------------------------------


class TestPartei:
    def test_construct_minimal(self) -> None:
        p: Partei = {
            "id": "p1",
            "name": "OG",
            "flaeche_qm": 85.0,
            "personen": 2,
            "bewohnt_ab": date(2020, 1, 1),
            "bewohnt_bis": None,
            "hinweis": None,
        }
        assert p["id"] == "p1"

    def test_has_all_documented_keys(self) -> None:
        hints = get_type_hints(Partei)
        assert set(hints) == {
            "id",
            "name",
            "flaeche_qm",
            "personen",
            "bewohnt_ab",
            "bewohnt_bis",
            "hinweis",
        }


class TestKostenposition:
    def test_has_all_documented_keys(self) -> None:
        hints = get_type_hints(Kostenposition)
        assert set(hints) == {
            "id",
            "bezeichnung",
            "kategorie",
            "zuordnung",
            "zuordnung_partei_id",
            "betragsmodus",
            "betrag_eur",
            "periodizitaet",
            "faelligkeit",
            "verbrauchs_entity",
            "einheitspreis_eur",
            "einheit",
            "grundgebuehr_eur_monat",
            "verteilung",
            "verbrauch_entities_pro_partei",
            "aktiv_ab",
            "aktiv_bis",
            "notiz",
        }

    def test_construct_pauschal_haus(self) -> None:
        kp: Kostenposition = {
            "id": "kp1",
            "bezeichnung": "Gebäudeversicherung",
            "kategorie": Kategorie.VERSICHERUNG,
            "zuordnung": Zuordnung.HAUS,
            "zuordnung_partei_id": None,
            "betragsmodus": Betragsmodus.PAUSCHAL,
            "betrag_eur": 450.0,
            "periodizitaet": Periodizitaet.JAEHRLICH,
            "faelligkeit": date(2026, 3, 15),
            "verbrauchs_entity": None,
            "einheitspreis_eur": None,
            "einheit": None,
            "grundgebuehr_eur_monat": None,
            "verteilung": Verteilung.FLAECHE,
            "verbrauch_entities_pro_partei": None,
            "aktiv_ab": None,
            "aktiv_bis": None,
            "notiz": None,
        }
        assert kp["kategorie"] is Kategorie.VERSICHERUNG


class TestAdHocKosten:
    def test_has_all_documented_keys(self) -> None:
        hints = get_type_hints(AdHocKosten)
        assert set(hints) == {
            "id",
            "bezeichnung",
            "kategorie",
            "betrag_eur",
            "datum",
            "zuordnung",
            "zuordnung_partei_id",
            "verteilung",
            "bezahlt_am",
            "notiz",
        }


class TestCoordinatorResultShapes:
    def test_position_attribution_keys(self) -> None:
        hints = get_type_hints(PositionAttribution)
        assert set(hints) == {
            "kostenposition_id",
            "bezeichnung",
            "kategorie",
            "anteil_eur_jahr",
            "verteilschluessel_verwendet",
            "error",
        }

    def test_partei_result_keys(self) -> None:
        hints = get_type_hints(ParteiResult)
        assert set(hints) == {
            "partei",
            "monat_aktuell_eur",
            "jahr_aktuell_eur",
            "jahr_budget_eur",
            "pro_kategorie_jahr_eur",
            "naechste_faelligkeit",
            "positionen",
        }

    def test_haus_result_keys(self) -> None:
        hints = get_type_hints(HausResult)
        assert set(hints) == {
            "jahr_budget_eur",
            "jahr_aktuell_eur",
            "pro_kategorie_jahr_eur",
        }

    def test_coordinator_data_keys(self) -> None:
        hints = get_type_hints(CoordinatorData)
        assert set(hints) == {
            "computed_at",
            "jahr",
            "monat",
            "parteien",
            "haus",
        }

    def test_construct_minimal_coordinator_data(self) -> None:
        haus: HausResult = {
            "jahr_budget_eur": 0.0,
            "jahr_aktuell_eur": 0.0,
            "pro_kategorie_jahr_eur": {},
        }
        data: CoordinatorData = {
            "computed_at": datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
            "jahr": 2026,
            "monat": 4,
            "parteien": {},
            "haus": haus,
        }
        assert data["jahr"] == 2026
        assert data["haus"]["jahr_budget_eur"] == 0.0
