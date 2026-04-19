"""Tests for custom_components.hauskosten.distribution.

Authoritative oracle: docs/DISTRIBUTION.md. Every example in the spec has a
corresponding test here. Pure logic only -- no HA dependencies.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from custom_components.hauskosten.distribution import (
    allocate,
    distribute_with_rounding_fix,
)
from custom_components.hauskosten.models import Partei, Verteilung

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _partei(
    *,
    pid: str = "p1",
    name: str = "OG",
    flaeche_qm: float = 85.0,
    personen: int = 2,
    bewohnt_ab: date = date(2020, 1, 1),
    bewohnt_bis: date | None = None,
    hinweis: str | None = None,
) -> Partei:
    """Return a Partei TypedDict with sensible defaults."""
    return {
        "id": pid,
        "name": name,
        "flaeche_qm": flaeche_qm,
        "personen": personen,
        "bewohnt_ab": bewohnt_ab,
        "bewohnt_bis": bewohnt_bis,
        "hinweis": hinweis,
    }


STICHTAG_2026 = date(2026, 6, 1)


# ---------------------------------------------------------------------------
# distribute_with_rounding_fix
# ---------------------------------------------------------------------------


class TestDistributeWithRoundingFix:
    """Tests for the rounding helper used by all weighted allocations."""

    @pytest.mark.parametrize(
        ("betrag", "gewichte", "expected"),
        [
            (100.0, {"a": 1.0, "b": 1.0}, {"a": 50.0, "b": 50.0}),
            (100.0, {"a": 3.0, "b": 1.0}, {"a": 75.0, "b": 25.0}),
            (
                100.0,
                {"a": 1.0, "b": 2.0, "c": 3.0},
                {"a": 16.67, "b": 33.33, "c": 50.00},
            ),
            # 100 / 3 = 33.333... -> largest raw keeps the rest
            (
                100.0,
                {"a": 1.0, "b": 1.0, "c": 1.0},
                {"a": 33.34, "b": 33.33, "c": 33.33},
            ),
        ],
    )
    def test_splits_and_preserves_sum(
        self,
        betrag: float,
        gewichte: dict[str, float],
        expected: dict[str, float],
    ) -> None:
        result = distribute_with_rounding_fix(betrag, gewichte)
        assert result == expected
        assert round(sum(result.values()), 2) == betrag

    def test_zero_betrag_yields_zero_shares(self) -> None:
        result = distribute_with_rounding_fix(0.0, {"a": 1.0, "b": 3.0})
        assert result == {"a": 0.0, "b": 0.0}

    def test_negative_rounding_diff_goes_to_largest(self) -> None:
        # Raw: a=33.3333, b=66.6667 -> rounded 33.33 + 66.67 = 100.00 ok
        # Use values that round up:
        # 10 / 7 = 1.4285... splits 3/7 and 4/7 -> 4.29, 5.71 sums to 10.00
        result = distribute_with_rounding_fix(10.0, {"a": 3.0, "b": 4.0})
        assert round(sum(result.values()), 2) == 10.0

    def test_empty_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="weights"):
            distribute_with_rounding_fix(100.0, {})

    def test_zero_total_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="weights"):
            distribute_with_rounding_fix(100.0, {"a": 0.0, "b": 0.0})

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            distribute_with_rounding_fix(100.0, {"a": 1.0, "b": -1.0})

    def test_negative_betrag_raises(self) -> None:
        with pytest.raises(ValueError, match="betrag"):
            distribute_with_rounding_fix(-1.0, {"a": 1.0})

    def test_single_key_gets_full_amount(self) -> None:
        assert distribute_with_rounding_fix(99.99, {"only": 7.0}) == {"only": 99.99}


# ---------------------------------------------------------------------------
# DIREKT
# ---------------------------------------------------------------------------


class TestAllocateDirekt:
    """Tests for the DIREKT distribution key."""

    def test_routes_full_amount_to_target_partei(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2", flaeche_qm=65.0, personen=1)
        result = allocate(
            500.0,
            [p1, p2],
            key=Verteilung.DIREKT,
            stichtag=STICHTAG_2026,
            extra={"zuordnung_partei_id": "p2"},
        )
        assert result == {"p1": 0.0, "p2": 500.0}

    def test_zero_amount_still_returns_all_parties(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        result = allocate(
            0.0,
            [p1, p2],
            key=Verteilung.DIREKT,
            stichtag=STICHTAG_2026,
            extra={"zuordnung_partei_id": "p1"},
        )
        assert result == {"p1": 0.0, "p2": 0.0}

    def test_missing_extra_raises(self) -> None:
        p1 = _partei(pid="p1")
        with pytest.raises(ValueError, match="zuordnung_partei_id"):
            allocate(
                100.0,
                [p1],
                key=Verteilung.DIREKT,
                stichtag=STICHTAG_2026,
                extra=None,
            )

    def test_missing_zuordnung_key_raises(self) -> None:
        p1 = _partei(pid="p1")
        with pytest.raises(ValueError, match="zuordnung_partei_id"):
            allocate(
                100.0,
                [p1],
                key=Verteilung.DIREKT,
                stichtag=STICHTAG_2026,
                extra={"something_else": 1},
            )

    def test_unknown_partei_id_raises(self) -> None:
        p1 = _partei(pid="p1")
        with pytest.raises(ValueError, match="unknown"):
            allocate(
                100.0,
                [p1],
                key=Verteilung.DIREKT,
                stichtag=STICHTAG_2026,
                extra={"zuordnung_partei_id": "p_missing"},
            )


# ---------------------------------------------------------------------------
# GLEICH
# ---------------------------------------------------------------------------


class TestAllocateGleich:
    """Tests for the GLEICH (equal/per-head) distribution key."""

    def test_two_active_parties_split_equally(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 50.0, "p2": 50.0}

    def test_three_parties_rounding_distributes_correctly(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        p3 = _partei(pid="p3")
        result = allocate(
            100.0,
            [p1, p2, p3],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
        )
        assert round(sum(result.values()), 2) == 100.0
        # All shares within one cent of 33.33
        for v in result.values():
            assert v in (33.33, 33.34)

    def test_inactive_partei_gets_zero(self) -> None:
        p1 = _partei(pid="p1")
        p_inactive = _partei(
            pid="p2",
            bewohnt_ab=date(2020, 1, 1),
            bewohnt_bis=date(2025, 12, 31),
        )
        result = allocate(
            100.0,
            [p1, p_inactive],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_partei_not_yet_active_gets_zero(self) -> None:
        p1 = _partei(pid="p1")
        p_future = _partei(pid="p2", bewohnt_ab=date(2030, 1, 1))
        result = allocate(
            100.0,
            [p1, p_future],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_no_active_parties_raises(self) -> None:
        p_past = _partei(
            pid="p1",
            bewohnt_ab=date(2010, 1, 1),
            bewohnt_bis=date(2015, 12, 31),
        )
        with pytest.raises(ValueError, match="keine aktiven parteien"):
            allocate(
                100.0,
                [p_past],
                key=Verteilung.GLEICH,
                stichtag=STICHTAG_2026,
            )

    def test_empty_partei_list_raises(self) -> None:
        with pytest.raises(ValueError, match="keine aktiven parteien"):
            allocate(
                100.0,
                [],
                key=Verteilung.GLEICH,
                stichtag=STICHTAG_2026,
            )

    def test_mieterwechsel_time_weighted(self) -> None:
        # OG active all of 2026, DG_alt Jan-Jun, DG_neu Jul-Dec
        og = _partei(pid="og", bewohnt_ab=date(2020, 1, 1))
        dg_alt = _partei(
            pid="dg_alt",
            bewohnt_ab=date(2020, 1, 1),
            bewohnt_bis=date(2026, 6, 30),
        )
        dg_neu = _partei(
            pid="dg_neu",
            bewohnt_ab=date(2026, 7, 1),
        )
        stichtag = date(2026, 12, 31)
        result = allocate(
            300.0,
            [og, dg_alt, dg_neu],
            key=Verteilung.GLEICH,
            stichtag=stichtag,
            extra={
                "effektive_tage": {"og": 365, "dg_alt": 181, "dg_neu": 184},
            },
        )
        # Weights: og=1.0, dg_alt=181/365, dg_neu=184/365; total = 1 + 1 = 2.0
        # OG share = 300 * (365/730) = 150, DG_alt = 300 * 181/730 ≈ 74.38
        # DG_neu = 300 * 184/730 ≈ 75.62
        assert round(sum(result.values()), 2) == 300.0
        assert result["og"] == pytest.approx(150.0, abs=0.02)
        assert result["dg_alt"] == pytest.approx(74.38, abs=0.02)
        assert result["dg_neu"] == pytest.approx(75.62, abs=0.02)


# ---------------------------------------------------------------------------
# FLAECHE
# ---------------------------------------------------------------------------


class TestAllocateFlaeche:
    """Tests for the FLAECHE (by square meters) distribution key."""

    def test_happy_path_from_spec_example(self) -> None:
        # docs/DISTRIBUTION.md example: Versicherung 450 EUR/a, OG 85, DG 65
        og = _partei(pid="og", flaeche_qm=85.0)
        dg = _partei(pid="dg", flaeche_qm=65.0)
        result = allocate(
            450.0,
            [og, dg],
            key=Verteilung.FLAECHE,
            stichtag=STICHTAG_2026,
        )
        assert result == {"og": 255.0, "dg": 195.0}

    def test_equal_area_splits_equally(self) -> None:
        p1 = _partei(pid="p1", flaeche_qm=80.0)
        p2 = _partei(pid="p2", flaeche_qm=80.0)
        result = allocate(
            200.0,
            [p1, p2],
            key=Verteilung.FLAECHE,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 100.0}

    def test_inactive_partei_is_excluded(self) -> None:
        p1 = _partei(pid="p1", flaeche_qm=50.0)
        p2 = _partei(
            pid="p2",
            flaeche_qm=50.0,
            bewohnt_bis=date(2025, 12, 31),
        )
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.FLAECHE,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_zero_flaeche_partei_gets_zero_when_others_have_area(self) -> None:
        p1 = _partei(pid="p1", flaeche_qm=100.0)
        p2 = _partei(pid="p2", flaeche_qm=0.0)
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.FLAECHE,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_all_zero_flaeche_raises(self) -> None:
        p1 = _partei(pid="p1", flaeche_qm=0.0)
        p2 = _partei(pid="p2", flaeche_qm=0.0)
        with pytest.raises(ValueError, match="gesamt_qm"):
            allocate(
                100.0,
                [p1, p2],
                key=Verteilung.FLAECHE,
                stichtag=STICHTAG_2026,
            )

    def test_rounding_sum_stays_exact(self) -> None:
        p1 = _partei(pid="p1", flaeche_qm=33.0)
        p2 = _partei(pid="p2", flaeche_qm=33.0)
        p3 = _partei(pid="p3", flaeche_qm=33.0)
        result = allocate(
            100.0,
            [p1, p2, p3],
            key=Verteilung.FLAECHE,
            stichtag=STICHTAG_2026,
        )
        assert round(sum(result.values()), 2) == 100.0

    def test_mieterwechsel_from_spec(self) -> None:
        # docs/DISTRIBUTION.md Mieterwechsel example (450 EUR/a)
        og = _partei(pid="og", flaeche_qm=85.0)
        dg_alt = _partei(
            pid="dg_alt",
            flaeche_qm=65.0,
            bewohnt_bis=date(2026, 6, 30),
        )
        dg_neu = _partei(
            pid="dg_neu",
            flaeche_qm=65.0,
            bewohnt_ab=date(2026, 7, 1),
        )
        stichtag = date(2026, 12, 31)
        result = allocate(
            450.0,
            [og, dg_alt, dg_neu],
            key=Verteilung.FLAECHE,
            stichtag=stichtag,
            extra={
                "effektive_tage": {"og": 365, "dg_alt": 181, "dg_neu": 184},
            },
        )
        # Expected from spec: OG=255, DG_alt≈96.69, DG_neu≈98.31
        assert round(sum(result.values()), 2) == 450.0
        assert result["og"] == pytest.approx(255.0, abs=0.02)
        assert result["dg_alt"] == pytest.approx(96.69, abs=0.02)
        assert result["dg_neu"] == pytest.approx(98.31, abs=0.02)


# ---------------------------------------------------------------------------
# PERSONEN
# ---------------------------------------------------------------------------


class TestAllocatePersonen:
    """Tests for the PERSONEN (by headcount) distribution key."""

    def test_happy_path_from_spec_example(self) -> None:
        # docs/DISTRIBUTION.md example: Muell 240 EUR/a, OG 2P, DG 1P
        og = _partei(pid="og", personen=2)
        dg = _partei(pid="dg", personen=1)
        result = allocate(
            240.0,
            [og, dg],
            key=Verteilung.PERSONEN,
            stichtag=STICHTAG_2026,
        )
        assert result == {"og": 160.0, "dg": 80.0}

    def test_leerstand_partei_gets_zero(self) -> None:
        # Leerstand in one party, others carry the load
        p1 = _partei(pid="p1", personen=2)
        leer = _partei(pid="leer", personen=0)
        result = allocate(
            200.0,
            [p1, leer],
            key=Verteilung.PERSONEN,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 200.0, "leer": 0.0}

    def test_all_leerstand_raises(self) -> None:
        p1 = _partei(pid="p1", personen=0)
        p2 = _partei(pid="p2", personen=0)
        with pytest.raises(ValueError, match="gesamt_p"):
            allocate(
                100.0,
                [p1, p2],
                key=Verteilung.PERSONEN,
                stichtag=STICHTAG_2026,
            )

    def test_inactive_party_is_skipped(self) -> None:
        p1 = _partei(pid="p1", personen=1)
        p2 = _partei(
            pid="p2",
            personen=1,
            bewohnt_bis=date(2025, 12, 31),
        )
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.PERSONEN,
            stichtag=STICHTAG_2026,
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_time_weighted_personen(self) -> None:
        og = _partei(pid="og", personen=2)
        dg = _partei(pid="dg", personen=1)
        result = allocate(
            300.0,
            [og, dg],
            key=Verteilung.PERSONEN,
            stichtag=date(2026, 12, 31),
            extra={"effektive_tage": {"og": 365, "dg": 183}},
        )
        # Weights: og = 2*1 = 2, dg = 1*183/365 ≈ 0.5014
        # Total ≈ 2.5014, og share ≈ 300 * 2/2.5014 ≈ 239.83
        assert round(sum(result.values()), 2) == 300.0
        assert result["og"] > result["dg"]


# ---------------------------------------------------------------------------
# VERBRAUCH_SUBZAEHLER
# ---------------------------------------------------------------------------


class TestAllocateVerbrauchSubzaehler:
    """Tests for the VERBRAUCH_SUBZAEHLER (sub-meter) distribution key."""

    def test_happy_path_proportional_to_consumption(self) -> None:
        og = _partei(pid="og")
        dg = _partei(pid="dg")
        # OG consumed 60 m3, DG 40 m3 of 100 m3 total -> OG gets 60% of 250 EUR
        result = allocate(
            250.0,
            [og, dg],
            key=Verteilung.VERBRAUCH_SUBZAEHLER,
            stichtag=STICHTAG_2026,
            extra={"verbrauch_pro_partei": {"og": 60.0, "dg": 40.0}},
        )
        assert result == {"og": 150.0, "dg": 100.0}

    def test_missing_extra_raises(self) -> None:
        og = _partei(pid="og")
        with pytest.raises(ValueError, match="verbrauch_pro_partei"):
            allocate(
                100.0,
                [og],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra=None,
            )

    def test_missing_key_in_extra_raises(self) -> None:
        og = _partei(pid="og")
        with pytest.raises(ValueError, match="verbrauch_pro_partei"):
            allocate(
                100.0,
                [og],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra={"other": 1},
            )

    def test_missing_partei_verbrauch_raises(self) -> None:
        og = _partei(pid="og")
        dg = _partei(pid="dg")
        with pytest.raises(ValueError, match="missing"):
            allocate(
                100.0,
                [og, dg],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra={"verbrauch_pro_partei": {"og": 10.0}},
            )

    def test_zero_total_verbrauch_raises(self) -> None:
        og = _partei(pid="og")
        dg = _partei(pid="dg")
        with pytest.raises(ValueError, match="gesamt_v"):
            allocate(
                100.0,
                [og, dg],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra={"verbrauch_pro_partei": {"og": 0.0, "dg": 0.0}},
            )

    def test_negative_verbrauch_raises(self) -> None:
        og = _partei(pid="og")
        with pytest.raises(ValueError, match="negative"):
            allocate(
                100.0,
                [og],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra={"verbrauch_pro_partei": {"og": -5.0}},
            )

    def test_verbrauch_pro_partei_not_a_dict_raises(self) -> None:
        og = _partei(pid="og")
        with pytest.raises(ValueError, match="must be a dict"):
            allocate(
                100.0,
                [og],
                key=Verteilung.VERBRAUCH_SUBZAEHLER,
                stichtag=STICHTAG_2026,
                extra={"verbrauch_pro_partei": "nonsense"},
            )

    def test_inactive_partei_with_consumption_still_included(self) -> None:
        # Sub-meters are measured independently; an inactive partei that
        # still consumed (pre-move-out) keeps its share. The caller decides
        # the period -- we trust the numbers passed in.
        og = _partei(pid="og")
        dg_alt = _partei(
            pid="dg_alt",
            bewohnt_bis=date(2025, 12, 31),
        )
        result = allocate(
            100.0,
            [og, dg_alt],
            key=Verteilung.VERBRAUCH_SUBZAEHLER,
            stichtag=STICHTAG_2026,
            extra={"verbrauch_pro_partei": {"og": 50.0, "dg_alt": 50.0}},
        )
        assert round(sum(result.values()), 2) == 100.0


# ---------------------------------------------------------------------------
# Dispatcher / generic
# ---------------------------------------------------------------------------


class TestAllocateDispatcher:
    """Cross-cutting tests on the allocate() entrypoint."""

    def test_negative_betrag_raises(self) -> None:
        p1 = _partei(pid="p1")
        with pytest.raises(ValueError, match="betrag"):
            allocate(
                -1.0,
                [p1],
                key=Verteilung.GLEICH,
                stichtag=STICHTAG_2026,
            )

    def test_unknown_verteilung_raises(self) -> None:
        p1 = _partei(pid="p1")
        with pytest.raises(ValueError, match="unsupported"):
            allocate(
                100.0,
                [p1],
                key="unknown_key",  # type: ignore[arg-type]
                stichtag=STICHTAG_2026,
            )

    def test_duplicate_partei_id_raises(self) -> None:
        p1 = _partei(pid="dup")
        p2 = _partei(pid="dup", name="Other")
        with pytest.raises(ValueError, match="duplicate"):
            allocate(
                100.0,
                [p1, p2],
                key=Verteilung.GLEICH,
                stichtag=STICHTAG_2026,
            )

    @pytest.mark.parametrize(
        "key",
        [
            Verteilung.GLEICH,
            Verteilung.FLAECHE,
            Verteilung.PERSONEN,
        ],
    )
    def test_effektive_tage_defaults_to_active(
        self,
        key: Verteilung,
    ) -> None:
        """Without effektive_tage extra, active parties get full weight."""
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        result = allocate(
            100.0,
            [p1, p2],
            key=key,
            stichtag=STICHTAG_2026,
        )
        assert round(sum(result.values()), 2) == 100.0
        assert set(result.keys()) == {"p1", "p2"}

    def test_extra_effektive_tage_zero_excludes_partei(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
            extra={"effektive_tage": {"p1": 365, "p2": 0}},
        )
        assert result == {"p1": 100.0, "p2": 0.0}

    def test_all_effektive_tage_zero_raises(self) -> None:
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        with pytest.raises(ValueError, match="keine aktiven parteien"):
            allocate(
                100.0,
                [p1, p2],
                key=Verteilung.GLEICH,
                stichtag=STICHTAG_2026,
                extra={"effektive_tage": {"p1": 0, "p2": 0}},
            )

    def test_effektive_tage_not_a_dict_falls_back_to_stichtag(self) -> None:
        """A malformed effektive_tage entry is ignored in favour of stichtag."""
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
            extra={"effektive_tage": "nonsense"},
        )
        assert result == {"p1": 50.0, "p2": 50.0}

    def test_partial_effektive_tage_falls_back_for_missing_partei(self) -> None:
        """Parties missing from effektive_tage map fall back to stichtag logic."""
        p1 = _partei(pid="p1")
        p2 = _partei(pid="p2")
        # Only p1 has explicit weighting; p2 uses stichtag (active)
        result = allocate(
            100.0,
            [p1, p2],
            key=Verteilung.GLEICH,
            stichtag=STICHTAG_2026,
            extra={"effektive_tage": {"p1": 365}},
        )
        # Both should contribute equally: weight(p1)=1.0, weight(p2)=1.0
        assert result == {"p1": 50.0, "p2": 50.0}


# ---------------------------------------------------------------------------
# Fixture-based integration-ish test
# ---------------------------------------------------------------------------


def test_allocate_flaeche_with_conftest_fixture(
    sample_partei_og: dict[str, Any],
    sample_partei_dg: dict[str, Any],
    sample_kostenposition_versicherung: dict[str, Any],
) -> None:
    """Real MFH scenario using the shared conftest fixtures."""
    og: Partei = {
        "id": sample_partei_og["id"],
        "name": sample_partei_og["name"],
        "flaeche_qm": sample_partei_og["flaeche_qm"],
        "personen": sample_partei_og["personen"],
        "bewohnt_ab": sample_partei_og["bewohnt_ab"],
        "bewohnt_bis": sample_partei_og["bewohnt_bis"],
        "hinweis": sample_partei_og["hinweis"],
    }
    dg: Partei = {
        "id": sample_partei_dg["id"],
        "name": sample_partei_dg["name"],
        "flaeche_qm": sample_partei_dg["flaeche_qm"],
        "personen": sample_partei_dg["personen"],
        "bewohnt_ab": sample_partei_dg["bewohnt_ab"],
        "bewohnt_bis": sample_partei_dg["bewohnt_bis"],
        "hinweis": sample_partei_dg["hinweis"],
    }
    result = allocate(
        sample_kostenposition_versicherung["betrag_eur"],
        [og, dg],
        key=Verteilung.FLAECHE,
        stichtag=date(2026, 6, 1),
    )
    # 85/150 * 450 = 255, 65/150 * 450 = 195
    assert result == {"partei-og": 255.0, "partei-dg": 195.0}
