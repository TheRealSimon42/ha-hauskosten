"""Pure time and amount calculations for ha-hauskosten.

Authoritative spec: ``docs/ARCHITECTURE.md`` ("Pure Logic Modules") and
``docs/DATA_MODEL.md``. Every public function is a pure, synchronous helper
that the coordinator composes with :mod:`.distribution` to produce the
annual and per-period figures the sensor platform exposes.

This module is **free of Home Assistant imports** and uses only the standard
library plus the project's own ``models`` module. Date inputs are naive
``datetime.date`` values -- there is no notion of a time zone in the
accounting domain this integration covers.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from .models import Partei, Periodizitaet

__all__ = [
    "abschlag_ist_kosten",
    "abschlag_saldo",
    "abschlag_zeitraum_ende",
    "abschlaege_gezahlt",
    "active_in_period",
    "annualize",
    "days_overlap",
    "effektive_tage",
    "monthly_share",
    "next_due_date",
    "resolve_verbrauchs_betrag",
    "vergangene_monate",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Multiplier used to scale per-period amounts to a yearly baseline. Einmalig
# one-off costs have no recurring annual value and therefore map to zero.
_OCCURRENCES_PER_YEAR: dict[Periodizitaet, int] = {
    Periodizitaet.MONATLICH: 12,
    Periodizitaet.QUARTALSWEISE: 4,
    Periodizitaet.HALBJAEHRLICH: 2,
    Periodizitaet.JAEHRLICH: 1,
    Periodizitaet.EINMALIG: 0,
}


# Step in months between consecutive occurrences, used by ``next_due_date``.
# ``EINMALIG`` is absent on purpose -- one-off costs are handled separately.
_MONTHS_BETWEEN_OCCURRENCES: dict[Periodizitaet, int] = {
    Periodizitaet.MONATLICH: 1,
    Periodizitaet.QUARTALSWEISE: 3,
    Periodizitaet.HALBJAEHRLICH: 6,
    Periodizitaet.JAEHRLICH: 12,
}


# ---------------------------------------------------------------------------
# Amount calculations
# ---------------------------------------------------------------------------


def annualize(betrag: float, periodizitaet: Periodizitaet) -> float:
    """Scale a per-period amount to a yearly amount.

    Args:
        betrag: The amount charged per occurrence of ``periodizitaet``.
            Must be non-negative. Ints are accepted and promoted to float.
        periodizitaet: The cadence at which ``betrag`` recurs. ``EINMALIG``
            maps to ``0.0`` because a one-off cost has no recurring annual
            value -- the caller handles one-offs via ``next_due_date`` and
            ``active_in_period`` instead.

    Returns:
        The annualised amount in Euro, with no rounding applied.

    Raises:
        ValueError: If ``betrag`` is negative.
    """
    if betrag < 0:
        raise ValueError(f"betrag must be non-negative, got {betrag}")
    occurrences = _OCCURRENCES_PER_YEAR[periodizitaet]
    return float(betrag) * occurrences


def monthly_share(jahresbetrag: float) -> float:
    """Return one twelfth of an annual amount.

    Args:
        jahresbetrag: The annual amount in Euro. Must be non-negative.

    Returns:
        ``jahresbetrag / 12`` with full float precision (no rounding).

    Raises:
        ValueError: If ``jahresbetrag`` is negative.
    """
    if jahresbetrag < 0:
        raise ValueError(
            f"jahresbetrag must be non-negative, got {jahresbetrag}",
        )
    return float(jahresbetrag) / 12.0


def resolve_verbrauchs_betrag(
    einheitspreis_eur: float,
    verbrauch: float,
    grundgebuehr_eur_monat: float | None,
) -> float:
    """Compute the annual amount for a usage-based cost item.

    The formula is ``verbrauch * einheitspreis + 12 * grundgebuehr``. All
    inputs are annual-scoped -- ``verbrauch`` is the yearly total, the base
    fee is expressed per month and annualised internally.

    Args:
        einheitspreis_eur: Price per unit (EUR/m3, EUR/kWh, ...). Non-negative.
        verbrauch: Consumed amount in the unit matching ``einheitspreis_eur``.
            Non-negative.
        grundgebuehr_eur_monat: Optional monthly base fee in Euro. ``None``
            is treated as zero.

    Returns:
        Annual amount in Euro with full float precision.

    Raises:
        ValueError: If any numeric input is negative.
    """
    if einheitspreis_eur < 0:
        raise ValueError(
            f"einheitspreis_eur must be non-negative, got {einheitspreis_eur}",
        )
    if verbrauch < 0:
        raise ValueError(f"verbrauch must be non-negative, got {verbrauch}")
    grund = grundgebuehr_eur_monat or 0.0
    if grund < 0:
        raise ValueError(
            f"grundgebuehr_eur_monat must be non-negative, got {grund}",
        )
    return float(einheitspreis_eur) * float(verbrauch) + 12.0 * float(grund)


# ---------------------------------------------------------------------------
# Time-range helpers
# ---------------------------------------------------------------------------


def _shift_month(anchor: date, months: int) -> date:
    """Return ``anchor`` shifted by ``months`` with month-end clamping.

    When the target month has fewer days than ``anchor.day``, the result is
    clamped to the last valid day of that month. ``months`` may be negative.
    """
    year = anchor.year + (anchor.month - 1 + months) // 12
    month = (anchor.month - 1 + months) % 12 + 1
    last_day = monthrange(year, month)[1]
    day = min(anchor.day, last_day)
    return date(year, month, day)


def next_due_date(
    start: date,
    periodizitaet: Periodizitaet,
    reference: date,
) -> date | None:
    """Return the next due date on or after ``reference``.

    For recurring cadences, the occurrence dates are derived from ``start``
    in fixed month steps (1, 3, 6 or 12). Month-end days are clamped to the
    last day of shorter months (e.g. a 31-Jan anchor becomes 28/29-Feb).
    For ``EINMALIG`` the only candidate is ``start`` itself.

    Args:
        start: The first occurrence date. Its day-of-month anchors the
            recurrence.
        periodizitaet: The cadence.
        reference: The reference date; the result is the smallest due date
            ``d`` with ``d >= reference``.

    Returns:
        The next due date, or ``None`` when the cadence is ``EINMALIG`` and
        ``start`` is strictly before ``reference``.
    """
    if periodizitaet is Periodizitaet.EINMALIG:
        return start if start >= reference else None

    step_months = _MONTHS_BETWEEN_OCCURRENCES[periodizitaet]

    if start >= reference:
        return start

    # Coarse bound: how many whole year-pairs fit between the dates tells us
    # an upper number of step shifts to try. The loop is still bounded by
    # ``step_months`` in the worst case (about 12 iterations for monthly).
    current = start
    while current < reference:
        current = _shift_month(current, step_months)
    return current


def active_in_period(
    aktiv_ab: date | None,
    aktiv_bis: date | None,
    period_start: date,
    period_end: date,
) -> bool:
    """Return True when an open-ended interval overlaps a period.

    ``aktiv_ab`` and ``aktiv_bis`` are both inclusive. ``None`` stands for
    an open bound (unbounded past / future).

    Args:
        aktiv_ab: Inclusive start of the activity interval, or ``None``.
        aktiv_bis: Inclusive end of the activity interval, or ``None``.
        period_start: Inclusive start of the period under inspection.
        period_end: Inclusive end of the period under inspection.

    Returns:
        ``True`` if the intervals share at least one day, else ``False``.

    Raises:
        ValueError: If ``period_end`` is before ``period_start`` or if
            ``aktiv_bis`` is before ``aktiv_ab``.
    """
    if period_end < period_start:
        raise ValueError("period_end must not be before period_start")
    if aktiv_ab is not None and aktiv_bis is not None and aktiv_bis < aktiv_ab:
        raise ValueError("aktiv_bis must not be before aktiv_ab")
    if aktiv_ab is not None and aktiv_ab > period_end:
        return False
    return not (aktiv_bis is not None and aktiv_bis < period_start)


def days_overlap(
    start: date,
    end: date | None,
    period_start: date,
    period_end: date,
) -> int:
    """Return the number of days an interval overlaps a period.

    Both interval and period use inclusive bounds. ``end`` may be ``None``
    to denote an open-ended interval. The result is always non-negative --
    a disjoint interval returns ``0``.

    Args:
        start: Inclusive start of the interval.
        end: Inclusive end of the interval, or ``None`` for open-ended.
        period_start: Inclusive start of the period.
        period_end: Inclusive end of the period.

    Returns:
        The inclusive day count of the intersection
        (``0`` for disjoint intervals).

    Raises:
        ValueError: If ``period_end`` is before ``period_start`` or if
            ``end`` is before ``start``.
    """
    if period_end < period_start:
        raise ValueError("period_end must not be before period_start")
    if end is not None and end < start:
        raise ValueError("interval end must not be before interval start")
    effective_end = period_end if end is None else min(end, period_end)
    effective_start = max(start, period_start)
    if effective_end < effective_start:
        return 0
    return (effective_end - effective_start).days + 1


def effektive_tage(
    partei: Partei,
    period_start: date,
    period_end: date,
) -> int:
    """Return the day count a Partei was active inside a period.

    Thin wrapper around :func:`days_overlap` for ``Partei`` records.
    Mirrors the algorithm described in ``docs/DISTRIBUTION.md`` and is used
    by :func:`.distribution.allocate` for time-weighted allocations.

    Args:
        partei: The Partei whose activity interval is evaluated. Uses
            ``bewohnt_ab`` and ``bewohnt_bis`` fields.
        period_start: Inclusive start of the period.
        period_end: Inclusive end of the period.

    Returns:
        Number of days the Partei was active inside the period.
    """
    return days_overlap(
        partei["bewohnt_ab"],
        partei["bewohnt_bis"],
        period_start,
        period_end,
    )


# ---------------------------------------------------------------------------
# Abschlag helpers (see issue #10 / docs/DATA_MODEL.md)
# ---------------------------------------------------------------------------


def abschlag_zeitraum_ende(zeitraum_start: date, dauer_monate: int) -> date:
    """Return the inclusive last day of an Abschlag reconciliation period.

    Args:
        zeitraum_start: First day of the reconciliation period.
        dauer_monate: Length of the period in months. Must be positive.

    Returns:
        The inclusive final date of the period, i.e. the day before the
        same calendar day ``dauer_monate`` months after ``zeitraum_start``.
        Month-end days shorter than ``zeitraum_start.day`` are clamped.

    Raises:
        ValueError: If ``dauer_monate`` is not positive.
    """
    if dauer_monate <= 0:
        raise ValueError(f"dauer_monate must be positive, got {dauer_monate}")
    next_period = _shift_month(zeitraum_start, dauer_monate)
    return date.fromordinal(next_period.toordinal() - 1)


def vergangene_monate(
    zeitraum_start: date,
    stichtag: date,
    dauer_monate: int,
) -> int:
    """Count the full months elapsed between ``zeitraum_start`` and ``stichtag``.

    A month counts as "elapsed" once the same calendar day one month later
    has passed. In other words a reconciliation period anchored on
    ``zeitraum_start`` is treated as a sequence of month-long chunks; we
    return how many of those chunks are complete at ``stichtag``.

    The result is clamped to ``[0, dauer_monate]`` -- a stichtag past the
    period end reports the full duration, and a stichtag before the start
    reports zero.

    Args:
        zeitraum_start: First day of the reconciliation period.
        stichtag: Reference date.
        dauer_monate: Upper bound on the returned count (period length in
            months).

    Returns:
        Integer in ``[0, dauer_monate]`` of completed months.

    Raises:
        ValueError: If ``dauer_monate`` is not positive.
    """
    if dauer_monate <= 0:
        raise ValueError(f"dauer_monate must be positive, got {dauer_monate}")
    if stichtag <= zeitraum_start:
        return 0
    months = (stichtag.year - zeitraum_start.year) * 12 + (
        stichtag.month - zeitraum_start.month
    )
    if stichtag.day < zeitraum_start.day:
        months -= 1
    return max(0, min(months, dauer_monate))


def abschlaege_gezahlt(
    monatlicher_abschlag_eur: float,
    zeitraum_start: date,
    dauer_monate: int,
    stichtag: date,
) -> float:
    """Return the cumulative prepayments due by ``stichtag``.

    Multiplies the monthly prepayment by the number of completed months in
    the reconciliation period. The result is not rounded -- the caller
    applies cent rounding at the sensor edge.

    Args:
        monatlicher_abschlag_eur: Monthly prepayment amount in Euro.
            Non-negative.
        zeitraum_start: First day of the reconciliation period.
        dauer_monate: Length of the period in months. Positive.
        stichtag: Reference date.

    Returns:
        Total amount prepaid so far in the current reconciliation period,
        in Euro.

    Raises:
        ValueError: If ``monatlicher_abschlag_eur`` is negative or
            ``dauer_monate`` is not positive.
    """
    if monatlicher_abschlag_eur < 0:
        raise ValueError(
            f"monatlicher_abschlag_eur must be non-negative, "
            f"got {monatlicher_abschlag_eur}",
        )
    monate = vergangene_monate(zeitraum_start, stichtag, dauer_monate)
    return float(monatlicher_abschlag_eur) * monate


def abschlag_ist_kosten(
    einheitspreis_eur: float,
    verbrauch: float,
    grundgebuehr_eur_monat: float | None,
    monate_aktiv: int,
) -> float:
    """Return the consumption-derived IST cost for an Abschlag period.

    Mirrors :func:`resolve_verbrauchs_betrag` but scales the base fee by an
    explicit number of active months (not the implicit 12), because
    reconciliation periods can be shorter than a year and can be evaluated
    mid-period.

    Args:
        einheitspreis_eur: Price per unit. Non-negative.
        verbrauch: Consumed amount over the period in the matching unit.
            Non-negative.
        grundgebuehr_eur_monat: Optional monthly base fee in Euro. ``None``
            is treated as zero.
        monate_aktiv: Number of months the position has been active in the
            current period. Non-negative.

    Returns:
        IST cost in Euro with full float precision.

    Raises:
        ValueError: If any numeric input is negative.
    """
    if einheitspreis_eur < 0:
        raise ValueError(
            f"einheitspreis_eur must be non-negative, got {einheitspreis_eur}",
        )
    if verbrauch < 0:
        raise ValueError(f"verbrauch must be non-negative, got {verbrauch}")
    if monate_aktiv < 0:
        raise ValueError(f"monate_aktiv must be non-negative, got {monate_aktiv}")
    grund = grundgebuehr_eur_monat or 0.0
    if grund < 0:
        raise ValueError(
            f"grundgebuehr_eur_monat must be non-negative, got {grund}",
        )
    return float(einheitspreis_eur) * float(verbrauch) + float(grund) * monate_aktiv


def abschlag_saldo(ist_eur: float, gezahlt_eur: float) -> float:
    """Return ``ist_eur - gezahlt_eur`` as the Abschlag balance.

    Positive values mean an expected additional payment ("Nachzahlung"),
    negative values mean credit ("Guthaben"). Cents rounding is applied
    because the balance is shown directly as a sensor state.

    Args:
        ist_eur: Metered cost over the period in Euro.
        gezahlt_eur: Cumulative prepayment total in Euro.

    Returns:
        Rounded difference in Euro (2 decimal places).
    """
    return round(float(ist_eur) - float(gezahlt_eur), 2)
