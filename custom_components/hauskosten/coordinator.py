"""DataUpdateCoordinator for the hauskosten integration.

Authoritative spec:
* ``docs/ARCHITECTURE.md`` -- "Coordinator (coordinator.py)" section
  describing the update cycle, state-change listener and pure aggregation.
* ``docs/DATA_MODEL.md``   -- :class:`CoordinatorData` / :class:`ParteiResult`
  / :class:`HausResult` / :class:`PositionAttribution` shapes.
* ``docs/DISTRIBUTION.md`` -- formulas invoked via :func:`.distribution.allocate`.
* ``AGENTS.md`` hard constraints:
    #1 -- we only read consumption entities, never re-log them.
    #3 -- no file I/O in this module; :class:`HauskostenStore` owns disk I/O.
    #4 -- every ``async`` error path logs via ``_LOGGER``.
    #5 -- consumption references use ``entity_id`` only, never ``device_id``.

The coordinator is a pure aggregation layer: it normalises config subentries
to the model types, collects ad-hoc costs from the store, reads state from
referenced consumption entities and delegates every arithmetic step to
:mod:`.calculations` and :mod:`.distribution`. The heavy lifting lives in
those pure-logic modules; this module only orchestrates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from . import distribution
from .calculations import (
    abschlaege_gezahlt,
    abschlag_ist_kosten,
    abschlag_saldo,
    active_in_period,
    annualize,
    effektive_tage,
    monthly_share,
    next_due_date,
    resolve_verbrauchs_betrag,
    vergangene_monate,
)
from .const import (
    DEFAULT_ABRECHNUNGSZEITRAUM_DAUER_MONATE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from .models import (
    Betragsmodus,
    CoordinatorData,
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

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

    from .storage import HauskostenStore

__all__ = ["HauskostenCoordinator"]

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinel state values that mean "no usable reading".
# ---------------------------------------------------------------------------

_UNUSABLE_STATES = frozenset({"unavailable", "unknown", ""})


class HauskostenCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Aggregates hauskosten data from subentries, store and entities.

    Instances are owned by ``async_setup_entry`` in :mod:`__init__` and live
    for the lifetime of the config entry. The class is intentionally thin:

    * ``_async_update_data`` turns subentries + store + entity reads into a
      :class:`CoordinatorData` snapshot.
    * ``async_setup_state_listener`` / ``async_shutdown_listener`` wire and
      tear down the state-change listener so consumption changes trigger a
      debounced refresh.

    The class delegates every formula to :mod:`.calculations` and
    :mod:`.distribution` so it stays testable without a running HA core.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: HauskostenStore,
    ) -> None:
        """Create a coordinator bound to one config entry.

        Args:
            hass: The Home Assistant instance.
            entry: The config entry holding the parteien / kostenpositionen
                subentries.
            store: The :class:`HauskostenStore` that exposes ad-hoc costs and
                payment timestamps; must already be loaded or auto-load on
                first access.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Hauskosten ({entry.title})",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self._store = store
        self._unsub_state_listener: Any = None

    # ------------------------------------------------------------------
    # Public listener management
    # ------------------------------------------------------------------

    @callback
    def async_setup_state_listener(self) -> None:
        """Register a state-change listener on all referenced entities.

        When the set of referenced consumption entities is empty (e.g. only
        pauschal costs are configured) no listener is installed. Calling this
        method while a listener is already active is idempotent -- the old
        listener is torn down first so the new one stays in sync with the
        current subentries.
        """
        self.async_shutdown_listener()
        entities = self._relevant_entities()
        if not entities:
            _LOGGER.debug("No consumption entities referenced; skipping state listener")
            return
        self._unsub_state_listener = async_track_state_change_event(
            self.hass, entities, self._handle_state_change
        )
        _LOGGER.debug("State listener tracking %d entities", len(entities))

    @callback
    def async_shutdown_listener(self) -> None:
        """Unsubscribe the state-change listener if active.

        Safe to call multiple times; becomes a no-op after the first call
        until :meth:`async_setup_state_listener` is invoked again.
        """
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None

    # ------------------------------------------------------------------
    # Hook: re-compute on entity state change
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Schedule a debounced refresh when a tracked entity changed.

        ``async_request_refresh`` is used rather than ``async_refresh`` so
        bursts of state changes collapse to a single recompute.
        """
        _ = event  # event content is not used; the entity list is already known
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------
    # Core update cycle
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> CoordinatorData:
        """Compute a fresh :class:`CoordinatorData` snapshot.

        Pipeline (see ``docs/ARCHITECTURE.md``):

        1. Load parteien + kostenpositionen from ``entry.subentries``.
        2. Load ad-hoc costs from the store.
        3. For each consumption kostenposition, resolve entity state
           into a usage value; missing / unusable states are flagged as
           per-position errors (not as a whole-update failure).
        4. For each ABSCHLAG kostenposition, query the recorder Statistics
           API for consumption over the reconciliation period (async; fed
           into the sync compute step as a pre-fetched map).
        5. Delegate to :mod:`.distribution` / :mod:`.calculations` to turn
           each position into a per-party share.
        6. Aggregate shares into the hierarchical output.

        Returns:
            A :class:`CoordinatorData` dict ready for the sensor platform.

        Raises:
            UpdateFailed: When an unexpected exception escapes the pipeline.
                Partial per-position errors are captured in
                :class:`PositionAttribution.error` and do not fail the update.
        """
        try:
            now = dt_util.now()
            kostenpositionen = self._collect_kostenpositionen()
            abschlag_verbrauch = await self._fetch_abschlag_verbrauch(
                kostenpositionen,
                now,
            )
            return self._compute(
                now=now,
                kostenpositionen=kostenpositionen,
                abschlag_verbrauch=abschlag_verbrauch,
            )
        except UpdateFailed:  # pragma: no cover - defensive passthrough
            raise
        except Exception as err:
            _LOGGER.exception("Unexpected error during hauskosten update")
            raise UpdateFailed(f"hauskosten compute failed: {err}") from err

    # ------------------------------------------------------------------
    # Statistics helpers
    # ------------------------------------------------------------------

    async def _fetch_abschlag_verbrauch(
        self,
        kostenpositionen: list[Kostenposition],
        now: datetime,
    ) -> dict[str, float | None]:
        """Return ``{kp_id: verbrauch_over_period or None}`` for ABSCHLAG kps.

        Non-ABSCHLAG positions are absent from the returned dict. Positions
        without a consumption entity or reconciliation anchor map to
        ``None``. Any recorder / statistics error is logged and also
        surfaces as ``None`` so the downstream aggregation can report the
        IST value as unavailable instead of crashing.
        """
        result: dict[str, float | None] = {}
        abschlag_positions = [
            kp for kp in kostenpositionen if kp["betragsmodus"] is Betragsmodus.ABSCHLAG
        ]
        if not abschlag_positions:
            return result
        # Import lazily: the recorder module pulls in a decent amount of
        # state; we only need it when at least one ABSCHLAG position is
        # configured.
        try:
            # Import the package (not ``get_instance`` directly) so mypy
            # doesn't trip over its missing ``__all__`` export; use the
            # attribute form at the call site instead.
            from homeassistant.components import recorder  # noqa: PLC0415
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                statistic_during_period,
            )
        except ImportError:  # pragma: no cover - recorder is a core dep
            _LOGGER.warning("recorder unavailable; abschlag IST disabled")
            return {kp["id"]: None for kp in abschlag_positions}

        try:
            # ``get_instance`` is public but not in recorder's ``__all__``;
            # the attr-defined ignore matches what HA core uses itself.
            recorder_instance = recorder.get_instance(self.hass)  # type: ignore[attr-defined]
        except Exception:
            _LOGGER.warning("recorder instance unavailable; abschlag IST disabled")
            return {kp["id"]: None for kp in abschlag_positions}

        for kp in abschlag_positions:
            entity_id = kp.get("verbrauchs_entity")
            zeitraum_start = kp.get("abrechnungszeitraum_start")
            if not entity_id or zeitraum_start is None:
                result[kp["id"]] = None
                continue
            start_dt = dt_util.as_utc(
                dt_util.start_of_local_day(datetime.combine(zeitraum_start, time.min))
            )
            end_dt = dt_util.as_utc(now)
            try:
                stats = await recorder_instance.async_add_executor_job(
                    statistic_during_period,
                    self.hass,
                    start_dt,
                    end_dt,
                    entity_id,
                    {"change"},
                    None,
                )
            except Exception:
                _LOGGER.exception(
                    "statistics fetch failed for abschlag kp=%s entity=%s",
                    kp["id"],
                    entity_id,
                )
                result[kp["id"]] = None
                continue
            change = stats.get("change") if isinstance(stats, dict) else None
            if change is None:
                _LOGGER.debug(
                    "no statistics change returned for abschlag kp=%s entity=%s",
                    kp["id"],
                    entity_id,
                )
                result[kp["id"]] = None
                continue
            try:
                result[kp["id"]] = float(change)
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "statistics change %r not numeric for kp=%s",
                    change,
                    kp["id"],
                )
                result[kp["id"]] = None
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute(
        self,
        *,
        now: datetime | None = None,
        kostenpositionen: list[Kostenposition] | None = None,
        abschlag_verbrauch: dict[str, float | None] | None = None,
    ) -> CoordinatorData:
        """Synchronous core of the update pipeline.

        Split out to keep ``_async_update_data`` focused on error handling
        and to make the function directly testable. Safe to call without a
        running event loop -- no ``await`` here, only state reads.

        Args:
            now: Injected clock; defaults to ``dt_util.now()``. Tests use
                this to avoid clock-dependent assertions.
            kostenpositionen: Optional pre-collected list; avoids re-reading
                subentries when ``_async_update_data`` already has them.
            abschlag_verbrauch: Pre-fetched ``{kp_id: verbrauch or None}``
                map produced by :meth:`_fetch_abschlag_verbrauch`; when
                omitted ABSCHLAG positions report the Statistics-missing
                error and produce ``None`` IST values.
        """
        if now is None:
            now = dt_util.now()
        if abschlag_verbrauch is None:
            abschlag_verbrauch = {}
        stichtag = now.date()
        jahr = now.year
        monat = now.month
        period_start = date(jahr, 1, 1)
        period_end = date(jahr, 12, 31)

        parteien = self._collect_parteien()
        if kostenpositionen is None:
            kostenpositionen = self._collect_kostenpositionen()

        # Pre-compute time-weighting once per party (used across positions).
        tage_map: dict[str, int] = {
            p["id"]: effektive_tage(p, period_start, period_end) for p in parteien
        }

        # Seed each party's aggregate state.
        partei_accum: dict[str, _ParteiAccumulator] = {
            p["id"]: _ParteiAccumulator(partei=p) for p in parteien
        }

        for kp in kostenpositionen:
            if not active_in_period(
                kp.get("aktiv_ab"),
                kp.get("aktiv_bis"),
                period_start,
                period_end,
            ):
                continue
            self._apply_kostenposition(
                kp,
                parteien=parteien,
                tage_map=tage_map,
                stichtag=stichtag,
                partei_accum=partei_accum,
                abschlag_verbrauch=abschlag_verbrauch,
            )

        for adhoc in self._store.adhoc_kosten:
            self._apply_adhoc(
                adhoc,
                parteien=parteien,
                tage_map=tage_map,
                stichtag=stichtag,
                partei_accum=partei_accum,
            )

        parteien_result: dict[str, ParteiResult] = {
            pid: acc.to_result() for pid, acc in partei_accum.items()
        }
        haus_result = _build_haus_result(parteien_result)
        return {
            "computed_at": dt_util.utcnow(),
            "jahr": jahr,
            "monat": monat,
            "parteien": parteien_result,
            "haus": haus_result,
        }

    # ------------------------------------------------------------------
    # Subentry collection
    # ------------------------------------------------------------------

    def _collect_parteien(self) -> list[Partei]:
        """Return all parteien subentries normalised to :class:`Partei` dicts."""
        return [
            _partei_from_subentry(s)
            for s in self._iter_subentries_of_type(SUBENTRY_PARTEI)
        ]

    def _collect_kostenpositionen(self) -> list[Kostenposition]:
        """Return all kostenposition subentries normalised to typed dicts."""
        return [
            _kostenposition_from_subentry(s)
            for s in self._iter_subentries_of_type(SUBENTRY_KOSTENPOSITION)
        ]

    def _iter_subentries_of_type(self, subentry_type: str) -> Iterable[ConfigSubentry]:
        """Yield config subentries matching the given ``subentry_type``."""
        entry = self.config_entry
        if entry is None:  # pragma: no cover - coordinator always has entry
            return
        for sub in entry.subentries.values():
            if sub.subentry_type == subentry_type:
                yield sub

    # ------------------------------------------------------------------
    # Listener scope
    # ------------------------------------------------------------------

    @callback
    def _relevant_entities(self) -> list[str]:
        """Return the sorted, deduplicated list of tracked entity IDs.

        Covers both the main ``verbrauchs_entity`` (used to compute the total
        amount) and the per-party ``verbrauch_entities_pro_partei`` entries
        (used by :class:`Verteilung.VERBRAUCH_SUBZAEHLER`).
        """
        ids: set[str] = set()
        for kp in self._collect_kostenpositionen():
            main = kp.get("verbrauchs_entity")
            if main:
                ids.add(main)
            per_party = kp.get("verbrauch_entities_pro_partei") or {}
            for eid in per_party.values():
                if eid:
                    ids.add(eid)
        return sorted(ids)

    # ------------------------------------------------------------------
    # Per-position processing
    # ------------------------------------------------------------------

    def _apply_kostenposition(
        self,
        kp: Kostenposition,
        *,
        parteien: list[Partei],
        tage_map: dict[str, int],
        stichtag: date,
        partei_accum: dict[str, _ParteiAccumulator],
        abschlag_verbrauch: dict[str, float | None],
    ) -> None:
        """Resolve a single kostenposition to per-party attributions."""
        # Resolve the annual amount first; verbrauch-based positions may
        # short-circuit into an error attribution if the source entity is
        # missing or unusable.
        amount_error: str | None = None
        annual_amount: float = 0.0
        betragsmodus = kp["betragsmodus"]
        abschlag_totals: _AbschlagTotals | None = None
        if betragsmodus is Betragsmodus.PAUSCHAL:
            annual_amount = _annualize_pauschal(kp)
        elif betragsmodus is Betragsmodus.VERBRAUCH:
            annual_amount, amount_error = self._resolve_verbrauchs_amount(kp)
        else:
            abschlag_totals, amount_error = _resolve_abschlag_totals(
                kp,
                stichtag=stichtag,
                verbrauch=abschlag_verbrauch.get(kp["id"]),
            )
            # The gezahlt total drives the yearly aggregates; IST / saldo
            # only flow into the dedicated abschlag_* fields per party.
            annual_amount = (
                abschlag_totals.gezahlt_total if abschlag_totals is not None else 0.0
            )

        extra, dist_error = self._build_allocation_extra(
            kp,
            parteien=parteien,
            tage_map=tage_map,
        )
        error = amount_error or dist_error

        shares: dict[str, float]
        ist_shares: dict[str, float] | None = None
        if error is not None:
            shares = {p["id"]: 0.0 for p in parteien}
        else:
            try:
                shares = distribution.allocate(
                    annual_amount,
                    parteien,
                    key=kp["verteilung"],
                    stichtag=stichtag,
                    extra=extra,
                )
                if (
                    abschlag_totals is not None
                    and abschlag_totals.ist_total is not None
                ):
                    ist_shares = distribution.allocate(
                        abschlag_totals.ist_total,
                        parteien,
                        key=kp["verteilung"],
                        stichtag=stichtag,
                        extra=extra,
                    )
            except ValueError as err:
                _LOGGER.warning(
                    "Distribution failed for kostenposition %s: %s",
                    kp["id"],
                    err,
                )
                error = f"distribution failed: {err}"
                shares = {p["id"]: 0.0 for p in parteien}
                ist_shares = None

        faelligkeit = _resolve_next_due(kp, stichtag)
        for p in parteien:
            pid = p["id"]
            gezahlt_eur: float | None = None
            ist_eur: float | None = None
            saldo_eur: float | None = None
            if abschlag_totals is not None and error is None:
                gezahlt_eur = shares[pid]
                if ist_shares is not None:
                    ist_eur = ist_shares[pid]
                    saldo_eur = abschlag_saldo(ist_eur, gezahlt_eur)
            attribution: PositionAttribution = {
                "kostenposition_id": kp["id"],
                "bezeichnung": kp["bezeichnung"],
                "kategorie": kp["kategorie"],
                "anteil_eur_jahr": shares[pid],
                "verteilschluessel_verwendet": kp["verteilung"],
                "error": error,
                "abschlag_gezahlt_eur_jahr": gezahlt_eur,
                "abschlag_ist_eur_jahr": ist_eur,
                "abschlag_saldo_eur_jahr": saldo_eur,
            }
            partei_accum[pid].add_position(attribution, faelligkeit)

    def _apply_adhoc(
        self,
        adhoc: dict[str, Any],
        *,
        parteien: list[Partei],
        tage_map: dict[str, int],
        stichtag: date,
        partei_accum: dict[str, _ParteiAccumulator],
    ) -> None:
        """Fold an ad-hoc cost record into the per-party accumulators."""
        betrag = float(adhoc.get("betrag_eur", 0.0))
        zuordnung = Zuordnung(adhoc["zuordnung"])
        verteilung = Verteilung(adhoc["verteilung"])
        kategorie = Kategorie(adhoc["kategorie"])

        extra: dict[str, Any] = {"effektive_tage": dict(tage_map)}
        if zuordnung is Zuordnung.PARTEI:
            extra["zuordnung_partei_id"] = adhoc.get("zuordnung_partei_id")

        error: str | None = None
        try:
            shares = distribution.allocate(
                betrag,
                parteien,
                key=verteilung,
                stichtag=stichtag,
                extra=extra,
            )
        except ValueError as err:
            _LOGGER.warning(
                "Ad-hoc allocation failed for id=%s: %s",
                adhoc.get("id"),
                err,
            )
            error = f"adhoc allocation failed: {err}"
            shares = {p["id"]: 0.0 for p in parteien}

        for p in parteien:
            pid = p["id"]
            attribution: PositionAttribution = {
                "kostenposition_id": adhoc["id"],
                "bezeichnung": adhoc.get("bezeichnung", ""),
                "kategorie": kategorie,
                "anteil_eur_jahr": shares[pid],
                "verteilschluessel_verwendet": verteilung,
                "error": error,
                "abschlag_gezahlt_eur_jahr": None,
                "abschlag_ist_eur_jahr": None,
                "abschlag_saldo_eur_jahr": None,
            }
            partei_accum[pid].add_position(attribution, faelligkeit=None)

    # ------------------------------------------------------------------
    # Amount helpers
    # ------------------------------------------------------------------

    def _resolve_verbrauchs_amount(
        self,
        kp: Kostenposition,
    ) -> tuple[float, str | None]:
        """Resolve the annual EUR amount for a ``VERBRAUCH`` kostenposition.

        Returns a ``(amount, error)`` tuple where ``error`` is ``None`` on
        success. On any failure (missing entity, unusable state, non-numeric
        reading) the amount falls back to ``0.0`` and the error message is
        propagated into the per-position attribution.
        """
        entity_id = kp.get("verbrauchs_entity")
        if not entity_id:
            return 0.0, "verbrauchs_entity missing"
        value = self._read_numeric_state(entity_id, context=kp["id"])
        if value is None:
            return 0.0, f"verbrauchs_entity unavailable: {entity_id}"
        einheitspreis = kp.get("einheitspreis_eur")
        if einheitspreis is None:
            return 0.0, "einheitspreis_eur missing"
        try:
            amount = resolve_verbrauchs_betrag(
                float(einheitspreis),
                value,
                kp.get("grundgebuehr_eur_monat"),
            )
        except ValueError as err:
            _LOGGER.warning("Invalid verbrauch inputs for %s: %s", kp["id"], err)
            return 0.0, f"verbrauch calc failed: {err}"
        return amount, None

    def _read_numeric_state(
        self,
        entity_id: str,
        *,
        context: str,
    ) -> float | None:
        """Read an HA entity state and coerce it to a float.

        Returns ``None`` and logs a warning when the entity is missing,
        the state is one of the unusable sentinels or the reading is not a
        valid number. Callers own the error attribution.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning(
                "Consumption entity %s not found (context=%s)",
                entity_id,
                context,
            )
            return None
        raw = state.state
        if raw in _UNUSABLE_STATES:
            _LOGGER.warning(
                "Consumption entity %s state is unusable: %r (context=%s)",
                entity_id,
                raw,
                context,
            )
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Consumption entity %s has non-numeric state %r (context=%s)",
                entity_id,
                raw,
                context,
            )
            return None

    def _build_allocation_extra(
        self,
        kp: Kostenposition,
        *,
        parteien: list[Partei],
        tage_map: dict[str, int],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Build the ``extra`` payload for :func:`distribution.allocate`.

        Returns a ``(extra, error)`` tuple. ``error`` is non-``None`` when
        ``VERBRAUCH_SUBZAEHLER`` requires per-party sub-meter readings and
        one of them cannot be resolved; the caller then marks the position
        as erroneous without invoking the distribution.
        """
        extra: dict[str, Any] = {"effektive_tage": dict(tage_map)}
        verteilung = kp["verteilung"]

        if kp["zuordnung"] is Zuordnung.PARTEI:
            extra["zuordnung_partei_id"] = kp.get("zuordnung_partei_id")

        if verteilung is Verteilung.VERBRAUCH_SUBZAEHLER:
            per_party = kp.get("verbrauch_entities_pro_partei") or {}
            readings: dict[str, float] = {}
            for p in parteien:
                pid = p["id"]
                entity_id = per_party.get(pid)
                if not entity_id:
                    return None, f"subzaehler entity missing for party {pid}"
                value = self._read_numeric_state(
                    entity_id,
                    context=f"{kp['id']}/{pid}",
                )
                if value is None:
                    return None, f"subzaehler entity unusable: {entity_id}"
                readings[pid] = value
            extra["verbrauch_pro_partei"] = readings

        return extra, None


# ---------------------------------------------------------------------------
# Subentry -> model normalisation
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    """Coerce a subentry field value to a :class:`datetime.date`.

    ``None`` passes through. ``date`` instances are returned as-is. ISO-8601
    strings are parsed. Anything else returns ``None`` so the caller can
    treat the field as absent.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            _LOGGER.warning("Invalid ISO date %r in subentry field", value)
            return None
    return None  # pragma: no cover - defensive, not produced by normal flows


def _partei_from_subentry(subentry: ConfigSubentry) -> Partei:
    """Normalise a ``partei``-typed config subentry to a :class:`Partei` dict.

    The subentry's ``subentry_id`` becomes the party ``id`` -- parties are
    persisted through config subentries, so there is no second identifier.
    """
    data = subentry.data
    bewohnt_ab = _parse_date(data.get("bewohnt_ab")) or date.min
    return {
        "id": subentry.subentry_id,
        "name": cast("str", data.get("name", "")),
        "flaeche_qm": float(data.get("flaeche_qm", 0.0)),
        "personen": int(data.get("personen", 0)),
        "bewohnt_ab": bewohnt_ab,
        "bewohnt_bis": _parse_date(data.get("bewohnt_bis")),
        "hinweis": cast("str | None", data.get("hinweis")),
    }


def _kostenposition_from_subentry(subentry: ConfigSubentry) -> Kostenposition:
    """Normalise a ``kostenposition`` subentry to a :class:`Kostenposition`."""
    data = subentry.data
    periodizitaet_raw = data.get("periodizitaet")
    einheit_raw = data.get("einheit")
    dauer_raw = data.get("abrechnungszeitraum_dauer_monate")
    dauer_monate: int | None
    if dauer_raw is None:
        dauer_monate = None
    else:
        try:
            dauer_monate = int(dauer_raw)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            _LOGGER.warning(
                "Invalid abrechnungszeitraum_dauer_monate %r in subentry %s",
                dauer_raw,
                subentry.subentry_id,
            )
            dauer_monate = None
    return {
        "id": subentry.subentry_id,
        "bezeichnung": cast("str", data.get("bezeichnung", "")),
        "kategorie": Kategorie(data.get("kategorie", "sonstiges")),
        "zuordnung": Zuordnung(data.get("zuordnung", "haus")),
        "zuordnung_partei_id": cast("str | None", data.get("zuordnung_partei_id")),
        "betragsmodus": Betragsmodus(data.get("betragsmodus", "pauschal")),
        "betrag_eur": _optional_float(data.get("betrag_eur")),
        "periodizitaet": (
            Periodizitaet(periodizitaet_raw) if periodizitaet_raw else None
        ),
        "faelligkeit": _parse_date(data.get("faelligkeit")),
        "verbrauchs_entity": cast("str | None", data.get("verbrauchs_entity")),
        "einheitspreis_eur": _optional_float(data.get("einheitspreis_eur")),
        "einheit": _einheit_from_raw(einheit_raw),
        "grundgebuehr_eur_monat": _optional_float(data.get("grundgebuehr_eur_monat")),
        "monatlicher_abschlag_eur": _optional_float(
            data.get("monatlicher_abschlag_eur")
        ),
        "abrechnungszeitraum_start": _parse_date(data.get("abrechnungszeitraum_start")),
        "abrechnungszeitraum_dauer_monate": dauer_monate,
        "verteilung": Verteilung(data.get("verteilung", "gleich")),
        "verbrauch_entities_pro_partei": cast(
            "dict[str, str] | None",
            data.get("verbrauch_entities_pro_partei"),
        ),
        "aktiv_ab": _parse_date(data.get("aktiv_ab")),
        "aktiv_bis": _parse_date(data.get("aktiv_bis")),
        "notiz": cast("str | None", data.get("notiz")),
    }


def _optional_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float`` or return ``None``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        _LOGGER.warning("Could not coerce %r to float", value)
        return None


def _einheit_from_raw(raw: Any) -> Any:
    """Return an :class:`Einheit` enum member or ``None``."""
    if raw is None or raw == "":
        return None
    # Import locally to keep the top-level import surface clean.
    from .models import Einheit  # noqa: PLC0415

    return Einheit(raw)


# ---------------------------------------------------------------------------
# Helpers used by the compute pipeline
# ---------------------------------------------------------------------------


def _annualize_pauschal(kp: Kostenposition) -> float:
    """Scale a pauschal kostenposition to an annual EUR amount.

    Missing fields map to ``0.0`` -- the config flow validates the required
    fields up front, so falling back to zero here is a defensive default
    rather than silent data loss.
    """
    betrag = kp.get("betrag_eur")
    periodizitaet = kp.get("periodizitaet")
    if (
        betrag is None or periodizitaet is None
    ):  # pragma: no cover - config flow guards this
        return 0.0
    return annualize(float(betrag), periodizitaet)


def _resolve_next_due(kp: Kostenposition, reference: date) -> date | None:
    """Return the next due date for a kostenposition, or ``None``.

    Uses the position's ``faelligkeit`` as the recurrence anchor and the
    ``periodizitaet`` as the cadence.
    """
    faelligkeit = kp.get("faelligkeit")
    periodizitaet = kp.get("periodizitaet")
    if faelligkeit is None or periodizitaet is None:
        return None
    return next_due_date(faelligkeit, periodizitaet, reference)


class _AbschlagTotals:
    """Container for the three Abschlag whole-house totals.

    ``gezahlt_total`` is always populated when the position is valid (zero
    until one month has elapsed). ``ist_total`` is ``None`` when no
    consumption sensor is configured or the Statistics API has no data.
    """

    __slots__ = ("gezahlt_total", "ist_total")

    def __init__(self, gezahlt_total: float, ist_total: float | None) -> None:
        """Store the pre-allocation totals."""
        self.gezahlt_total = gezahlt_total
        self.ist_total = ist_total


def _resolve_abschlag_totals(
    kp: Kostenposition,
    *,
    stichtag: date,
    verbrauch: float | None,
) -> tuple[_AbschlagTotals | None, str | None]:
    """Compute the house-wide gezahlt + IST totals for an ABSCHLAG position.

    Returns ``(totals, error)``. ``totals`` is ``None`` when the config is
    incomplete (monthly rate or reconciliation anchor missing) -- the
    caller surfaces the error into the per-position attribution.
    """
    monatlich = kp.get("monatlicher_abschlag_eur")
    zeitraum_start = kp.get("abrechnungszeitraum_start")
    if monatlich is None:
        return None, "monatlicher_abschlag_eur missing"
    if zeitraum_start is None:
        return None, "abrechnungszeitraum_start missing"
    dauer = (
        kp.get("abrechnungszeitraum_dauer_monate")
        or DEFAULT_ABRECHNUNGSZEITRAUM_DAUER_MONATE
    )
    try:
        gezahlt_total = abschlaege_gezahlt(
            float(monatlich),
            zeitraum_start,
            int(dauer),
            stichtag,
        )
    except ValueError as err:
        return None, f"abschlag gezahlt failed: {err}"

    ist_total: float | None = None
    einheitspreis = kp.get("einheitspreis_eur")
    if verbrauch is not None and einheitspreis is not None:
        monate = vergangene_monate(zeitraum_start, stichtag, int(dauer))
        try:
            ist_total = abschlag_ist_kosten(
                float(einheitspreis),
                float(verbrauch),
                kp.get("grundgebuehr_eur_monat"),
                monate,
            )
        except ValueError as err:
            _LOGGER.warning(
                "abschlag IST calc failed for %s: %s",
                kp["id"],
                err,
            )
            ist_total = None

    return _AbschlagTotals(gezahlt_total=gezahlt_total, ist_total=ist_total), None


# ---------------------------------------------------------------------------
# Accumulator helpers
# ---------------------------------------------------------------------------


class _ParteiAccumulator:
    """Mutable aggregate of positions attributed to one partei.

    Kept module-private because it is an implementation detail of
    :class:`HauskostenCoordinator._compute`. The frozen public output is a
    :class:`ParteiResult` obtained via :meth:`to_result`.
    """

    __slots__ = ("_next_due", "_partei", "_positions")

    def __init__(self, partei: Partei) -> None:
        """Initialise an empty accumulator bound to ``partei``."""
        self._partei = partei
        self._positions: list[PositionAttribution] = []
        self._next_due: date | None = None

    def add_position(
        self,
        attribution: PositionAttribution,
        faelligkeit: date | None,
    ) -> None:
        """Append a position and track the earliest upcoming due date."""
        self._positions.append(attribution)
        if faelligkeit is not None and (
            self._next_due is None or faelligkeit < self._next_due
        ):
            self._next_due = faelligkeit

    def to_result(self) -> ParteiResult:
        """Return the immutable :class:`ParteiResult` snapshot."""
        jahr_budget_raw = sum(
            p["anteil_eur_jahr"] for p in self._positions if p["error"] is None
        )
        jahr_budget = round(jahr_budget_raw, 2)
        pro_kategorie: dict[Kategorie, float] = {}
        for p in self._positions:
            if p["error"] is not None:
                continue
            cat = p["kategorie"]
            pro_kategorie[cat] = round(
                pro_kategorie.get(cat, 0.0) + p["anteil_eur_jahr"], 2
            )
        return {
            "partei": self._partei,
            "monat_aktuell_eur": round(monthly_share(jahr_budget), 2),
            "jahr_aktuell_eur": jahr_budget,
            "jahr_budget_eur": jahr_budget,
            "pro_kategorie_jahr_eur": pro_kategorie,
            "naechste_faelligkeit": self._next_due,
            "positionen": list(self._positions),
        }


def _build_haus_result(parteien: dict[str, ParteiResult]) -> HausResult:
    """Fold party-level results into house-wide totals."""
    jahr_budget = round(sum(p["jahr_budget_eur"] for p in parteien.values()), 2)
    jahr_aktuell = round(sum(p["jahr_aktuell_eur"] for p in parteien.values()), 2)
    pro_kategorie: dict[Kategorie, float] = {}
    for p in parteien.values():
        for cat, value in p["pro_kategorie_jahr_eur"].items():
            pro_kategorie[cat] = round(pro_kategorie.get(cat, 0.0) + value, 2)
    return {
        "jahr_budget_eur": jahr_budget,
        "jahr_aktuell_eur": jahr_aktuell,
        "pro_kategorie_jahr_eur": pro_kategorie,
    }
