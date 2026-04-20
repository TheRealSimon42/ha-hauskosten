"""Tests for custom_components.hauskosten.calculations.

Authoritative oracles:
* docs/ARCHITECTURE.md -- "Pure Logic Modules" lists the public surface.
* docs/DATA_MODEL.md   -- Periodizitaet semantics and edge cases.
* docs/DISTRIBUTION.md -- the time-weighting day formula we mirror here.

All tests are pure Python -- no Home Assistant imports, no ``hass`` fixture.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.hauskosten.calculations import (
    abschlag_ist_kosten,
    abschlag_saldo,
    abschlag_zeitraum_ende,
    abschlaege_gezahlt,
    active_in_period,
    annualize,
    days_overlap,
    effektive_tage,
    monthly_share,
    next_due_date,
    resolve_verbrauchs_betrag,
    vergangene_monate,
)
from custom_components.hauskosten.models import Partei, Periodizitaet

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


# ---------------------------------------------------------------------------
# annualize
# ---------------------------------------------------------------------------


class TestAnnualize:
    """Tests for annualize(): scale a per-period amount to a yearly amount."""

    @pytest.mark.parametrize(
        ("betrag", "periodizitaet", "expected"),
        [
            (100.0, Periodizitaet.MONATLICH, 1200.0),
            (100.0, Periodizitaet.QUARTALSWEISE, 400.0),
            (100.0, Periodizitaet.HALBJAEHRLICH, 200.0),
            (100.0, Periodizitaet.JAEHRLICH, 100.0),
            # One-off costs have no recurring annual value -> 0.0.
            (100.0, Periodizitaet.EINMALIG, 0.0),
        ],
    )
    def test_scales_by_frequency(
        self,
        betrag: float,
        periodizitaet: Periodizitaet,
        expected: float,
    ) -> None:
        assert annualize(betrag, periodizitaet) == pytest.approx(expected)

    def test_zero_betrag_always_yields_zero(self) -> None:
        for p in Periodizitaet:
            assert annualize(0.0, p) == 0.0

    def test_negative_betrag_raises(self) -> None:
        with pytest.raises(ValueError, match="betrag"):
            annualize(-1.0, Periodizitaet.MONATLICH)

    def test_non_float_betrag_is_accepted_as_int(self) -> None:
        # Ints are legal numeric inputs, promoted to float internally.
        assert annualize(10, Periodizitaet.MONATLICH) == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# monthly_share
# ---------------------------------------------------------------------------


class TestMonthlyShare:
    """Tests for monthly_share(): twelfth of an annual amount."""

    @pytest.mark.parametrize(
        ("jahresbetrag", "expected"),
        [
            (1200.0, 100.0),
            (0.0, 0.0),
            (450.0, 37.5),
            (1.0, 1.0 / 12.0),
        ],
    )
    def test_divides_by_twelve(
        self,
        jahresbetrag: float,
        expected: float,
    ) -> None:
        assert monthly_share(jahresbetrag) == pytest.approx(expected)

    def test_negative_jahresbetrag_raises(self) -> None:
        with pytest.raises(ValueError, match="jahresbetrag"):
            monthly_share(-0.01)


# ---------------------------------------------------------------------------
# next_due_date
# ---------------------------------------------------------------------------


class TestNextDueDate:
    """Tests for next_due_date(): next recurrence on or after reference."""

    @pytest.mark.parametrize(
        ("start", "periodizitaet", "reference", "expected"),
        [
            # MONATLICH: recurrence on the 15th of every month.
            (
                date(2026, 1, 15),
                Periodizitaet.MONATLICH,
                date(2026, 1, 1),
                date(2026, 1, 15),
            ),
            (
                date(2026, 1, 15),
                Periodizitaet.MONATLICH,
                date(2026, 1, 15),
                date(2026, 1, 15),
            ),
            (
                date(2026, 1, 15),
                Periodizitaet.MONATLICH,
                date(2026, 1, 16),
                date(2026, 2, 15),
            ),
            (
                date(2026, 1, 15),
                Periodizitaet.MONATLICH,
                date(2026, 7, 10),
                date(2026, 7, 15),
            ),
            # Month-end clamping: 31-Jan start, next is 28-Feb in 2026.
            (
                date(2026, 1, 31),
                Periodizitaet.MONATLICH,
                date(2026, 2, 1),
                date(2026, 2, 28),
            ),
            # Quarterly: every three months.
            (
                date(2026, 3, 15),
                Periodizitaet.QUARTALSWEISE,
                date(2026, 3, 15),
                date(2026, 3, 15),
            ),
            (
                date(2026, 3, 15),
                Periodizitaet.QUARTALSWEISE,
                date(2026, 4, 1),
                date(2026, 6, 15),
            ),
            (
                date(2026, 3, 15),
                Periodizitaet.QUARTALSWEISE,
                date(2026, 12, 31),
                date(2027, 3, 15),
            ),
            # Half-yearly: every six months.
            (
                date(2026, 3, 15),
                Periodizitaet.HALBJAEHRLICH,
                date(2026, 4, 1),
                date(2026, 9, 15),
            ),
            (
                date(2026, 3, 15),
                Periodizitaet.HALBJAEHRLICH,
                date(2026, 10, 1),
                date(2027, 3, 15),
            ),
            # Yearly: same date next year if reference passed this year's due.
            (
                date(2026, 3, 15),
                Periodizitaet.JAEHRLICH,
                date(2026, 1, 1),
                date(2026, 3, 15),
            ),
            (
                date(2026, 3, 15),
                Periodizitaet.JAEHRLICH,
                date(2026, 4, 1),
                date(2027, 3, 15),
            ),
            # Leap-day start stays on 28-Feb in non-leap years.
            (
                date(2024, 2, 29),
                Periodizitaet.JAEHRLICH,
                date(2025, 1, 1),
                date(2025, 2, 28),
            ),
        ],
    )
    def test_returns_next_occurrence(
        self,
        start: date,
        periodizitaet: Periodizitaet,
        reference: date,
        expected: date,
    ) -> None:
        assert next_due_date(start, periodizitaet, reference) == expected

    def test_einmalig_future_returns_start(self) -> None:
        assert next_due_date(
            date(2026, 5, 1),
            Periodizitaet.EINMALIG,
            date(2026, 1, 1),
        ) == date(2026, 5, 1)

    def test_einmalig_on_reference_returns_start(self) -> None:
        assert next_due_date(
            date(2026, 5, 1),
            Periodizitaet.EINMALIG,
            date(2026, 5, 1),
        ) == date(2026, 5, 1)

    def test_einmalig_past_returns_none(self) -> None:
        assert (
            next_due_date(
                date(2026, 5, 1),
                Periodizitaet.EINMALIG,
                date(2026, 5, 2),
            )
            is None
        )

    def test_reference_before_start_returns_start_for_recurring(self) -> None:
        # Recurrence hasn't begun yet -> first occurrence is the start date.
        assert next_due_date(
            date(2026, 6, 1),
            Periodizitaet.MONATLICH,
            date(2026, 1, 1),
        ) == date(2026, 6, 1)


# ---------------------------------------------------------------------------
# active_in_period
# ---------------------------------------------------------------------------


class TestActiveInPeriod:
    """Tests for active_in_period(): open-ended interval overlap check."""

    @pytest.mark.parametrize(
        ("aktiv_ab", "aktiv_bis", "period_start", "period_end", "expected"),
        [
            # Fully covered.
            (None, None, date(2026, 1, 1), date(2026, 12, 31), True),
            # aktiv_ab before period, aktiv_bis inside.
            (
                date(2025, 1, 1),
                date(2026, 6, 30),
                date(2026, 1, 1),
                date(2026, 12, 31),
                True,
            ),
            # aktiv_ab inside, aktiv_bis after period.
            (
                date(2026, 6, 1),
                date(2027, 12, 31),
                date(2026, 1, 1),
                date(2026, 12, 31),
                True,
            ),
            # No overlap: ends before period starts.
            (
                date(2025, 1, 1),
                date(2025, 12, 31),
                date(2026, 1, 1),
                date(2026, 12, 31),
                False,
            ),
            # No overlap: starts after period ends.
            (
                date(2027, 1, 1),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                False,
            ),
            # Boundary: ends on first day of period -> still overlaps.
            (
                date(2025, 1, 1),
                date(2026, 1, 1),
                date(2026, 1, 1),
                date(2026, 12, 31),
                True,
            ),
            # Boundary: starts on last day of period -> still overlaps.
            (
                date(2026, 12, 31),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                True,
            ),
            # Open-ended aktiv_ab, aktiv_bis before period start -> False.
            (
                None,
                date(2025, 12, 31),
                date(2026, 1, 1),
                date(2026, 12, 31),
                False,
            ),
            # Single-day period fully inside active interval.
            (
                date(2020, 1, 1),
                None,
                date(2026, 6, 15),
                date(2026, 6, 15),
                True,
            ),
        ],
    )
    def test_overlap_matrix(
        self,
        aktiv_ab: date | None,
        aktiv_bis: date | None,
        period_start: date,
        period_end: date,
        expected: bool,
    ) -> None:
        assert (
            active_in_period(aktiv_ab, aktiv_bis, period_start, period_end) is expected
        )

    def test_inverted_period_raises(self) -> None:
        with pytest.raises(ValueError, match="period"):
            active_in_period(
                None,
                None,
                date(2026, 12, 31),
                date(2026, 1, 1),
            )

    def test_inverted_active_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="aktiv"):
            active_in_period(
                date(2026, 7, 1),
                date(2026, 1, 1),
                date(2026, 1, 1),
                date(2026, 12, 31),
            )


# ---------------------------------------------------------------------------
# days_overlap
# ---------------------------------------------------------------------------


class TestDaysOverlap:
    """Tests for days_overlap(): inclusive day count of the intersection."""

    @pytest.mark.parametrize(
        ("start", "end", "period_start", "period_end", "expected"),
        [
            # Full 2026 overlap.
            (
                date(2020, 1, 1),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                365,
            ),
            # Leap-year 2024.
            (
                date(2020, 1, 1),
                None,
                date(2024, 1, 1),
                date(2024, 12, 31),
                366,
            ),
            # Jan-Jun tenant.
            (
                date(2020, 1, 1),
                date(2026, 6, 30),
                date(2026, 1, 1),
                date(2026, 12, 31),
                181,
            ),
            # Jul-Dec tenant.
            (
                date(2026, 7, 1),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                184,
            ),
            # Starts during period, open-ended.
            (
                date(2026, 6, 1),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                214,  # 30 (Jun) + 31 + 31 + 30 + 31 + 30 + 31 = 214
            ),
            # No overlap -> 0.
            (
                date(2027, 1, 1),
                None,
                date(2026, 1, 1),
                date(2026, 12, 31),
                0,
            ),
            # Interval ends before period starts -> 0.
            (
                date(2020, 1, 1),
                date(2025, 12, 31),
                date(2026, 1, 1),
                date(2026, 12, 31),
                0,
            ),
            # Single overlapping day.
            (
                date(2026, 6, 15),
                date(2026, 6, 15),
                date(2026, 1, 1),
                date(2026, 12, 31),
                1,
            ),
        ],
    )
    def test_intersection_days(
        self,
        start: date,
        end: date | None,
        period_start: date,
        period_end: date,
        expected: int,
    ) -> None:
        assert days_overlap(start, end, period_start, period_end) == expected

    def test_inverted_period_raises(self) -> None:
        with pytest.raises(ValueError, match="period"):
            days_overlap(
                date(2026, 1, 1),
                None,
                date(2026, 12, 31),
                date(2026, 1, 1),
            )

    def test_inverted_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="interval"):
            days_overlap(
                date(2026, 7, 1),
                date(2026, 1, 1),
                date(2026, 1, 1),
                date(2026, 12, 31),
            )


# ---------------------------------------------------------------------------
# effektive_tage
# ---------------------------------------------------------------------------


class TestEffektiveTage:
    """Tests for effektive_tage(): active-day count of a Partei in a period."""

    def test_full_year_tenant_365_in_normal_year(self) -> None:
        p = _partei()
        assert effektive_tage(p, date(2026, 1, 1), date(2026, 12, 31)) == 365

    def test_full_year_tenant_366_in_leap_year(self) -> None:
        p = _partei(bewohnt_ab=date(2020, 1, 1))
        assert effektive_tage(p, date(2024, 1, 1), date(2024, 12, 31)) == 366

    def test_first_half_year_tenant(self) -> None:
        p = _partei(
            bewohnt_ab=date(2020, 1, 1),
            bewohnt_bis=date(2026, 6, 30),
        )
        assert effektive_tage(p, date(2026, 1, 1), date(2026, 12, 31)) == 181

    def test_second_half_year_tenant(self) -> None:
        p = _partei(bewohnt_ab=date(2026, 7, 1))
        assert effektive_tage(p, date(2026, 1, 1), date(2026, 12, 31)) == 184

    def test_tenant_outside_period_zero(self) -> None:
        p = _partei(
            bewohnt_ab=date(2020, 1, 1),
            bewohnt_bis=date(2025, 12, 31),
        )
        assert effektive_tage(p, date(2026, 1, 1), date(2026, 12, 31)) == 0


# ---------------------------------------------------------------------------
# resolve_verbrauchs_betrag
# ---------------------------------------------------------------------------


class TestResolveVerbrauchsBetrag:
    """Tests for resolve_verbrauchs_betrag(): usage-based annual amount."""

    def test_only_verbrauch_times_einheitspreis(self) -> None:
        # 120 m3 * 3 EUR/m3 = 360 EUR, no base fee.
        assert resolve_verbrauchs_betrag(3.0, 120.0, None) == pytest.approx(360.0)

    def test_adds_annual_base_fee(self) -> None:
        # 120 m3 * 3 + 12 * 5 EUR/month = 360 + 60 = 420.
        assert resolve_verbrauchs_betrag(3.0, 120.0, 5.0) == pytest.approx(420.0)

    def test_zero_base_fee_same_as_none(self) -> None:
        assert resolve_verbrauchs_betrag(3.0, 120.0, 0.0) == pytest.approx(360.0)

    def test_zero_verbrauch_gives_only_base_fee(self) -> None:
        assert resolve_verbrauchs_betrag(3.0, 0.0, 10.0) == pytest.approx(120.0)

    @pytest.mark.parametrize(
        ("einheitspreis", "verbrauch", "grundgebuehr"),
        [
            (-1.0, 100.0, None),
            (1.0, -1.0, None),
            (1.0, 100.0, -1.0),
        ],
    )
    def test_negative_inputs_raise(
        self,
        einheitspreis: float,
        verbrauch: float,
        grundgebuehr: float | None,
    ) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            resolve_verbrauchs_betrag(einheitspreis, verbrauch, grundgebuehr)


# ---------------------------------------------------------------------------
# abschlag_zeitraum_ende
# ---------------------------------------------------------------------------


class TestAbschlagZeitraumEnde:
    """Reconciliation end date is the last day before the period rolls."""

    def test_full_year_january_start(self) -> None:
        assert abschlag_zeitraum_ende(date(2026, 1, 1), 12) == date(2026, 12, 31)

    def test_half_year_from_march(self) -> None:
        assert abschlag_zeitraum_ende(date(2026, 3, 1), 6) == date(2026, 8, 31)

    def test_anchored_on_31st_clamps_to_short_months(self) -> None:
        # 31-Jan -> add 1 month = 28-Feb (2026 not leap) -> end = 27-Feb
        assert abschlag_zeitraum_ende(date(2026, 1, 31), 1) == date(2026, 2, 27)

    def test_raises_on_non_positive_dauer(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            abschlag_zeitraum_ende(date(2026, 1, 1), 0)


# ---------------------------------------------------------------------------
# vergangene_monate
# ---------------------------------------------------------------------------


class TestVergangeneMonate:
    """Count full months elapsed since the reconciliation period started."""

    def test_stichtag_before_start_is_zero(self) -> None:
        assert vergangene_monate(date(2026, 3, 1), date(2026, 2, 15), 12) == 0

    def test_stichtag_equal_to_start_is_zero(self) -> None:
        assert vergangene_monate(date(2026, 3, 1), date(2026, 3, 1), 12) == 0

    def test_exact_month_boundary_counts(self) -> None:
        # 1-Jan to 1-Feb = exactly one month elapsed.
        assert vergangene_monate(date(2026, 1, 1), date(2026, 2, 1), 12) == 1

    def test_mid_month_counts_prior_full_months_only(self) -> None:
        # 1-Jan anchor, 10-Mar -> Jan + Feb full, March still running.
        assert vergangene_monate(date(2026, 1, 1), date(2026, 3, 10), 12) == 2

    def test_anchor_day_not_yet_reached_drops_current_month(self) -> None:
        # 15-Jan anchor, 10-Feb -> Jan not fully closed (15-Jan to 14-Feb).
        assert vergangene_monate(date(2026, 1, 15), date(2026, 2, 10), 12) == 0
        # 15-Jan anchor, 15-Feb -> exactly one full month.
        assert vergangene_monate(date(2026, 1, 15), date(2026, 2, 15), 12) == 1

    def test_capped_at_duration(self) -> None:
        # Past the end of the period -> report full duration, not more.
        assert vergangene_monate(date(2026, 1, 1), date(2028, 6, 1), 12) == 12

    def test_raises_on_non_positive_dauer(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            vergangene_monate(date(2026, 1, 1), date(2026, 6, 1), 0)


# ---------------------------------------------------------------------------
# abschlaege_gezahlt
# ---------------------------------------------------------------------------


class TestAbschlaegeGezahlt:
    """Cumulative prepayment = monthly amount * elapsed months."""

    def test_half_year_elapsed(self) -> None:
        # 50 EUR / month, 1-Jan anchor, stichtag 1-Jul -> 6 months * 50 = 300.
        got = abschlaege_gezahlt(50.0, date(2026, 1, 1), 12, date(2026, 7, 1))
        assert got == pytest.approx(300.0)

    def test_before_start_is_zero(self) -> None:
        got = abschlaege_gezahlt(50.0, date(2026, 3, 1), 12, date(2026, 2, 1))
        assert got == 0.0

    def test_past_period_end_caps_at_full_year(self) -> None:
        # 50 EUR, 12-month period that ended before stichtag -> 600, not more.
        got = abschlaege_gezahlt(50.0, date(2025, 1, 1), 12, date(2027, 1, 1))
        assert got == pytest.approx(600.0)

    def test_zero_amount_returns_zero(self) -> None:
        got = abschlaege_gezahlt(0.0, date(2026, 1, 1), 12, date(2026, 6, 1))
        assert got == 0.0

    def test_negative_amount_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            abschlaege_gezahlt(-10.0, date(2026, 1, 1), 12, date(2026, 6, 1))


# ---------------------------------------------------------------------------
# abschlag_ist_kosten
# ---------------------------------------------------------------------------


class TestAbschlagIstKosten:
    """IST cost = price * usage + base fee * active months."""

    def test_happy_path_half_year_with_base_fee(self) -> None:
        # 30 m3 * 3 EUR + 5 EUR * 6 months = 90 + 30 = 120
        got = abschlag_ist_kosten(3.0, 30.0, 5.0, 6)
        assert got == pytest.approx(120.0)

    def test_no_base_fee_defaults_to_zero(self) -> None:
        got = abschlag_ist_kosten(3.0, 30.0, None, 6)
        assert got == pytest.approx(90.0)

    def test_zero_consumption_only_base_fee(self) -> None:
        got = abschlag_ist_kosten(3.0, 0.0, 5.0, 4)
        assert got == pytest.approx(20.0)

    def test_zero_months_active_only_consumption(self) -> None:
        # Base fee disappears when zero months have elapsed.
        got = abschlag_ist_kosten(3.0, 30.0, 5.0, 0)
        assert got == pytest.approx(90.0)

    @pytest.mark.parametrize(
        ("preis", "verbrauch", "grund", "monate"),
        [
            (-1.0, 10.0, None, 6),
            (3.0, -1.0, None, 6),
            (3.0, 10.0, -1.0, 6),
            (3.0, 10.0, None, -1),
        ],
    )
    def test_negative_inputs_raise(
        self,
        preis: float,
        verbrauch: float,
        grund: float | None,
        monate: int,
    ) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            abschlag_ist_kosten(preis, verbrauch, grund, monate)


# ---------------------------------------------------------------------------
# abschlag_saldo
# ---------------------------------------------------------------------------


class TestAbschlagSaldo:
    """Saldo = ist - gezahlt. Positive means under-paid."""

    def test_nachzahlung_positive(self) -> None:
        assert abschlag_saldo(560.0, 300.0) == 260.0

    def test_guthaben_negative(self) -> None:
        assert abschlag_saldo(240.0, 300.0) == -60.0

    def test_ausgeglichen_is_zero(self) -> None:
        assert abschlag_saldo(500.0, 500.0) == 0.0

    def test_rounds_to_two_decimals(self) -> None:
        assert abschlag_saldo(100.125, 50.005) == 50.12
