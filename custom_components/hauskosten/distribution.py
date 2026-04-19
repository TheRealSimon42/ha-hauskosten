"""Pure distribution algorithms for ha-hauskosten.

Authoritative spec: ``docs/DISTRIBUTION.md``. Every formula implemented here
has a one-to-one correspondence with an example in that document.

This module is **free of Home Assistant imports** and uses only the standard
library plus the project's own ``models`` module. All functions are pure and
synchronous; mutation of input collections is forbidden.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import Partei, Verteilung

__all__ = [
    "allocate",
    "distribute_with_rounding_fix",
]


# ---------------------------------------------------------------------------
# Rounding helper
# ---------------------------------------------------------------------------


def distribute_with_rounding_fix(
    betrag: float,
    weights: dict[str, float],
) -> dict[str, float]:
    """Distribute ``betrag`` across keys proportional to ``weights``.

    Each share is rounded to two decimal places. Any rounding difference is
    added to the key with the highest raw share so that the sum of rounded
    values equals ``betrag`` exactly (to cent precision).

    Args:
        betrag: The total amount to distribute. Must be non-negative.
        weights: Mapping ``{key: weight}``. Weights must be non-negative and
            their sum must be positive.

    Returns:
        Mapping ``{key: share}`` with each share rounded to two decimals.
        ``sum(result.values()) == round(betrag, 2)``.

    Raises:
        ValueError: If ``betrag`` is negative, ``weights`` is empty, contains
            a negative value, or sums to zero.
    """
    if betrag < 0:
        raise ValueError(f"betrag must be non-negative, got {betrag}")
    if not weights:
        raise ValueError("weights must not be empty")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights must not be negative")
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("weights must sum to a positive value")

    raw = {pid: betrag * w / total_weight for pid, w in weights.items()}
    rounded = {pid: round(v, 2) for pid, v in raw.items()}
    diff = round(betrag - sum(rounded.values()), 2)
    if diff != 0:
        # Assign rounding remainder to the party with the highest raw share
        largest = max(raw, key=lambda k: raw[k])
        rounded[largest] = round(rounded[largest] + diff, 2)
    return rounded


# ---------------------------------------------------------------------------
# Active-party helpers
# ---------------------------------------------------------------------------


def _is_active(partei: Partei, stichtag: date) -> bool:
    """Return True if the party is active on the given reference date."""
    if partei["bewohnt_ab"] > stichtag:
        return False
    bis = partei["bewohnt_bis"]
    return bis is None or bis >= stichtag


def _effective_days_factor(
    partei_id: str,
    extra: dict[str, Any] | None,
) -> float | None:
    """Return the time-weighting factor (0.0-1.0+) for a party, or None.

    When ``extra`` provides an ``effektive_tage`` mapping, the factor is
    ``days / 365``. Missing keys yield ``None`` so the caller can decide how
    to interpret "not time-weighted".
    """
    if extra is None:
        return None
    tage_map = extra.get("effektive_tage")
    if not isinstance(tage_map, dict):
        return None
    if partei_id not in tage_map:
        return None
    days = tage_map[partei_id]
    return float(days) / 365.0


def _ensure_unique_ids(parteien: list[Partei]) -> None:
    """Raise ValueError if ``parteien`` contains duplicate IDs."""
    seen: set[str] = set()
    for p in parteien:
        pid = p["id"]
        if pid in seen:
            raise ValueError(f"duplicate partei id: {pid}")
        seen.add(pid)


# ---------------------------------------------------------------------------
# Algorithm: DIREKT
# ---------------------------------------------------------------------------


def _allocate_direkt(
    betrag: float,
    parteien: list[Partei],
    *,
    extra: dict[str, Any] | None,
) -> dict[str, float]:
    """Route the full amount to a single target party."""
    if extra is None or "zuordnung_partei_id" not in extra:
        raise ValueError("DIREKT requires extra['zuordnung_partei_id']")
    target_id = extra["zuordnung_partei_id"]
    known_ids = {p["id"] for p in parteien}
    if target_id not in known_ids:
        raise ValueError(f"unknown zuordnung_partei_id: {target_id}")
    return {
        p["id"]: round(betrag, 2) if p["id"] == target_id else 0.0 for p in parteien
    }


# ---------------------------------------------------------------------------
# Algorithms: GLEICH / FLAECHE / PERSONEN (weight-based family)
# ---------------------------------------------------------------------------


def _base_weight(partei: Partei, key: Verteilung) -> float:
    """Return the unweighted base value for a party under ``key``."""
    if key is Verteilung.GLEICH:
        return 1.0
    if key is Verteilung.FLAECHE:
        return float(partei["flaeche_qm"])
    if key is Verteilung.PERSONEN:
        return float(partei["personen"])
    # Safety net: unreachable because dispatcher validates first
    raise ValueError(f"unsupported key for base weight: {key}")  # pragma: no cover


def _partei_time_factor(
    partei: Partei,
    stichtag: date,
    extra: dict[str, Any] | None,
) -> float:
    """Return a party's time-weighting factor (>= 0.0).

    Uses ``extra['effektive_tage']`` if present for that party, else falls
    back to ``1.0`` for parties active on ``stichtag`` and ``0.0`` otherwise.
    """
    explicit = _effective_days_factor(partei["id"], extra)
    if explicit is not None:
        return explicit
    return 1.0 if _is_active(partei, stichtag) else 0.0


def _allocate_weighted(
    betrag: float,
    parteien: list[Partei],
    *,
    key: Verteilung,
    stichtag: date,
    extra: dict[str, Any] | None,
) -> dict[str, float]:
    """Generic weighted allocation covering GLEICH / FLAECHE / PERSONEN."""
    time_factors: dict[str, float] = {
        p["id"]: _partei_time_factor(p, stichtag, extra) for p in parteien
    }
    weights: dict[str, float] = {
        p["id"]: _base_weight(p, key) * time_factors[p["id"]] for p in parteien
    }

    total_weight = sum(weights.values())
    if total_weight <= 0:
        # Differentiate the failure mode for helpful error messages
        any_active = any(f > 0 for f in time_factors.values())
        if not any_active:
            raise ValueError("keine aktiven parteien")
        if key is Verteilung.FLAECHE:
            raise ValueError("gesamt_qm is zero; cannot distribute by flaeche")
        if key is Verteilung.PERSONEN:
            raise ValueError("gesamt_p is zero; cannot distribute by personen")
        raise ValueError("keine aktiven parteien")  # pragma: no cover

    # Include zero-weight parties explicitly at 0.0
    non_zero = {k: v for k, v in weights.items() if v > 0}
    distributed = distribute_with_rounding_fix(betrag, non_zero)
    return {p["id"]: distributed.get(p["id"], 0.0) for p in parteien}


# ---------------------------------------------------------------------------
# Algorithm: VERBRAUCH_SUBZAEHLER
# ---------------------------------------------------------------------------


def _allocate_verbrauch_subzaehler(
    betrag: float,
    parteien: list[Partei],
    *,
    extra: dict[str, Any] | None,
) -> dict[str, float]:
    """Distribute proportional to measured consumption per party."""
    if extra is None or "verbrauch_pro_partei" not in extra:
        raise ValueError("VERBRAUCH_SUBZAEHLER requires extra['verbrauch_pro_partei']")
    verbrauch_map = extra["verbrauch_pro_partei"]
    if not isinstance(verbrauch_map, dict):
        # ValueError over TypeError: same style as other input validation here
        raise ValueError("verbrauch_pro_partei must be a dict")  # noqa: TRY004

    weights: dict[str, float] = {}
    for p in parteien:
        pid = p["id"]
        if pid not in verbrauch_map:
            raise ValueError(f"verbrauch_pro_partei missing partei: {pid}")
        value = float(verbrauch_map[pid])
        if value < 0:
            raise ValueError(f"verbrauch_pro_partei has negative value for {pid}")
        weights[pid] = value

    total = sum(weights.values())
    if total <= 0:
        raise ValueError("gesamt_v is zero; cannot distribute by subzaehler")

    non_zero = {k: v for k, v in weights.items() if v > 0}
    distributed = distribute_with_rounding_fix(betrag, non_zero)
    return {p["id"]: distributed.get(p["id"], 0.0) for p in parteien}


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def allocate(
    betrag_eur_jahr: float,
    parteien: list[Partei],
    *,
    key: Verteilung,
    stichtag: date,
    extra: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Distribute an annual amount across parties using ``key``.

    This is the single public entry point for all distribution algorithms.
    The formulas are documented in ``docs/DISTRIBUTION.md``.

    Args:
        betrag_eur_jahr: Annual total amount in Euro. Must be non-negative.
        parteien: All parties known to the current entry. Whether they are
            "active" for the purpose of this allocation is determined by
            ``stichtag`` and/or the ``extra['effektive_tage']`` map.
        key: Which distribution algorithm to use.
        stichtag: Reference date used for active-party detection.
        extra: Algorithm-specific auxiliary data:
            * ``DIREKT``: ``{"zuordnung_partei_id": str}`` required.
            * ``GLEICH`` / ``FLAECHE`` / ``PERSONEN``: optional
              ``{"effektive_tage": {partei_id: days}}`` for time-weighting.
            * ``VERBRAUCH_SUBZAEHLER``: ``{"verbrauch_pro_partei":
              {partei_id: value}}`` required.

    Returns:
        Mapping ``{partei_id: anteil_eur_jahr}`` covering **every** party in
        ``parteien``. Parties with zero weight receive 0.0. The sum of the
        values equals ``round(betrag_eur_jahr, 2)``.

    Raises:
        ValueError: For negative amounts, unsupported keys, duplicate party
            IDs, missing required ``extra`` fields, inactive-only party sets,
            or degenerate weight totals.
    """
    if betrag_eur_jahr < 0:
        raise ValueError(f"betrag must be non-negative, got {betrag_eur_jahr}")
    _ensure_unique_ids(parteien)

    if key is Verteilung.DIREKT:
        return _allocate_direkt(betrag_eur_jahr, parteien, extra=extra)
    if key in (Verteilung.GLEICH, Verteilung.FLAECHE, Verteilung.PERSONEN):
        return _allocate_weighted(
            betrag_eur_jahr,
            parteien,
            key=key,
            stichtag=stichtag,
            extra=extra,
        )
    if key is Verteilung.VERBRAUCH_SUBZAEHLER:
        return _allocate_verbrauch_subzaehler(betrag_eur_jahr, parteien, extra=extra)
    raise ValueError(f"unsupported verteilung key: {key!r}")
