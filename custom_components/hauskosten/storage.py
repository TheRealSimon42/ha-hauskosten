"""Persistent storage layer for ha-hauskosten.

Authoritative specs:
* ``docs/ARCHITECTURE.md`` -- Storage Layer section. One ``Store`` per config
  entry, holding only data that subentries cannot model (ad-hoc costs and
  payment history).
* ``docs/DATA_MODEL.md`` -- :class:`AdHocKosten` schema and schema-versioning
  rules. Schema-breaking changes require bumping :data:`.const.STORAGE_VERSION`
  and adding a branch to :meth:`HauskostenStore._async_migrate_func`.

Hard constraints honoured here (see ``AGENTS.md``):

* #3 -- File I/O is encapsulated in this module. The coordinator's update
  callback never touches the filesystem directly; it reads state via the
  public getters on :class:`HauskostenStore`.
* #4 -- Every ``async`` method has an ``_LOGGER`` error path.

This module is intentionally thin: CRUD + (de)serialisation only. No
business logic, no distribution math, no time arithmetic. Those live in
:mod:`.distribution` and :mod:`.calculations`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any, TypedDict, cast

from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import AdHocKosten

__all__ = ["HauskostenStore", "StoredData"]

_LOGGER = logging.getLogger(__name__)


class StoredData(TypedDict):
    """Persisted payload shape.

    Fields:
        ad_hoc_kosten: List of :class:`AdHocKosten` records serialised with
            ISO date strings; they are converted back to :class:`datetime.date`
            on load.
        paid_records: Mapping ``{kostenposition_id: bezahlt_am_iso}`` that
            records when a cost item was marked paid via the
            ``hauskosten.mark_paid`` service.
    """

    ad_hoc_kosten: list[dict[str, Any]]
    paid_records: dict[str, str]


# Date-bearing keys in an AdHocKosten record. Kept in one place so
# serialisation and deserialisation cannot drift apart.
_ADHOC_DATE_KEYS: tuple[str, ...] = ("datum", "bezahlt_am")


def _serialise_adhoc(item: AdHocKosten | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of an AdHocKosten record.

    Args:
        item: Source record; ``date`` instances are converted to ISO strings.

    Returns:
        Shallow copy where every ``date`` value on a known date key has been
        replaced with its ISO-8601 string representation. ``None`` is passed
        through unchanged.
    """
    out: dict[str, Any] = dict(item)
    for key in _ADHOC_DATE_KEYS:
        value = out.get(key)
        if isinstance(value, date):
            out[key] = value.isoformat()
    return out


def _deserialise_adhoc(raw: dict[str, Any]) -> dict[str, Any]:
    """Return an AdHocKosten-shaped dict with ``date`` fields rehydrated.

    Args:
        raw: Dict as read from the JSON store.

    Returns:
        Shallow copy with ISO date strings converted back to
        :class:`datetime.date`. Keys that are missing or ``None`` pass
        through unchanged.
    """
    out: dict[str, Any] = dict(raw)
    for key in _ADHOC_DATE_KEYS:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = date.fromisoformat(value)
            except ValueError:
                _LOGGER.warning(
                    "Invalid ISO date %r for key %s in ad-hoc record %s",
                    value,
                    key,
                    raw.get("id"),
                )
    return out


class _HauskostenStoreImpl(Store[StoredData]):
    """Thin :class:`Store` subclass used to hook schema migrations.

    Kept private so external callers always go through
    :class:`HauskostenStore`, which owns the in-memory cache and the
    higher-level mutation API.
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> StoredData:
        """Migrate an older payload to the current schema.

        The current schema is v1. Older versions (v0 or earlier) are treated
        as "unknown shape" and only the fields we recognise are carried over;
        everything else is dropped. Once a v2 schema exists, add a dedicated
        branch that upgrades v1 -> v2.

        Args:
            old_major_version: The major version that wrote the payload.
            old_minor_version: The minor version that wrote the payload.
            old_data: Raw payload as read from disk.

        Returns:
            A payload conforming to :class:`StoredData` at the current
            version.
        """
        _LOGGER.info(
            "Migrating hauskosten store from v%s.%s to v%s",
            old_major_version,
            old_minor_version,
            STORAGE_VERSION,
        )
        ad_hoc_raw = old_data.get("ad_hoc_kosten")
        paid_raw = old_data.get("paid_records")
        migrated: StoredData = {
            "ad_hoc_kosten": list(ad_hoc_raw) if isinstance(ad_hoc_raw, list) else [],
            "paid_records": dict(paid_raw) if isinstance(paid_raw, dict) else {},
        }
        return migrated


class HauskostenStore:
    """Persistence wrapper for a single config entry.

    One instance per ``ConfigEntry.entry_id``; the underlying storage key is
    ``{DOMAIN}.{entry_id}`` so each entry gets its own JSON file in
    ``.storage/``.

    The store persists:

    * :class:`AdHocKosten` records added via ``hauskosten.add_einmalig``.
    * Payment-history timestamps written by ``hauskosten.mark_paid``.

    It does **not** persist parteien or kostenpositionen; those live as
    config subentries.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create a store bound to the given config entry.

        Args:
            hass: The Home Assistant instance the store lives in.
            entry_id: The ``ConfigEntry.entry_id`` this store belongs to.
                Used as the suffix of the on-disk storage key.
        """
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store[StoredData] = _HauskostenStoreImpl(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
            atomic_writes=True,
        )
        self._adhoc: list[dict[str, Any]] = []
        self._paid: dict[str, date] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def key(self) -> str:
        """Return the underlying storage key (for tests / diagnostics)."""
        return self._store.key

    @property
    def version(self) -> int:
        """Return the storage schema version."""
        return self._store.version

    @property
    def adhoc_kosten(self) -> list[dict[str, Any]]:
        """Return a defensive copy of the ad-hoc cost list.

        Mutating the returned list does not affect the internal state; any
        change must go through :meth:`async_add_adhoc` /
        :meth:`async_remove_adhoc` so persistence stays in sync.
        """
        return [dict(item) for item in self._adhoc]

    @property
    def paid_records(self) -> dict[str, date]:
        """Return a defensive copy of the payment-history mapping."""
        return dict(self._paid)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted state from disk.

        Idempotent: repeated calls after the first successful load are
        no-ops. On first load, a missing file results in empty lists and
        dicts, matching a fresh install.
        """
        if self._loaded:
            return
        try:
            data = await self._store.async_load()
        except Exception:
            _LOGGER.exception(
                "Failed to load hauskosten store for entry %s", self._entry_id
            )
            raise
        self._adhoc = []
        self._paid = {}
        if data is not None:
            raw_adhoc = data.get("ad_hoc_kosten")
            if isinstance(raw_adhoc, list):
                self._adhoc = [
                    _deserialise_adhoc(entry)
                    for entry in raw_adhoc
                    if isinstance(entry, dict)
                ]
            else:
                _LOGGER.warning(
                    "Stored ad_hoc_kosten is not a list (entry=%s) - ignoring",
                    self._entry_id,
                )
            raw_paid = data.get("paid_records")
            if isinstance(raw_paid, dict):
                for kp_id, raw_date in raw_paid.items():
                    parsed = self._parse_date(raw_date, context=f"paid[{kp_id}]")
                    if parsed is not None:
                        self._paid[kp_id] = parsed
            else:
                _LOGGER.warning(
                    "Stored paid_records is not a dict (entry=%s) - ignoring",
                    self._entry_id,
                )
        self._loaded = True
        _LOGGER.debug(
            "Loaded hauskosten store entry=%s adhoc=%d paid=%d",
            self._entry_id,
            len(self._adhoc),
            len(self._paid),
        )

    async def async_save(self) -> None:
        """Persist the current in-memory state to disk.

        All mutation methods call this implicitly; it is also exposed for
        explicit flushes (e.g. at shutdown).
        """
        await self._ensure_loaded()
        payload: StoredData = {
            "ad_hoc_kosten": [_serialise_adhoc(item) for item in self._adhoc],
            "paid_records": {kp_id: d.isoformat() for kp_id, d in self._paid.items()},
        }
        try:
            await self._store.async_save(payload)
        except Exception:
            _LOGGER.exception(
                "Failed to save hauskosten store for entry %s", self._entry_id
            )
            raise
        _LOGGER.debug(
            "Saved hauskosten store entry=%s adhoc=%d paid=%d",
            self._entry_id,
            len(self._adhoc),
            len(self._paid),
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def async_add_adhoc(self, kosten: AdHocKosten | dict[str, Any]) -> None:
        """Add an ad-hoc cost item and persist immediately.

        Args:
            kosten: The :class:`AdHocKosten` record to append. The ``id``
                field must be unique across existing ad-hoc records.

        Raises:
            ValueError: If an ad-hoc record with the same ``id`` already
                exists.
        """
        await self._ensure_loaded()
        kosten_id = cast("str", kosten["id"])
        if any(item["id"] == kosten_id for item in self._adhoc):
            _LOGGER.error(
                "Refusing to add duplicate ad-hoc id=%s (entry=%s)",
                kosten_id,
                self._entry_id,
            )
            raise ValueError(f"duplicate ad-hoc id: {kosten_id}")
        self._adhoc.append(dict(kosten))
        await self.async_save()

    async def async_remove_adhoc(self, kosten_id: str) -> None:
        """Remove an ad-hoc cost item by id and persist immediately.

        Args:
            kosten_id: The id of the record to remove.

        Raises:
            KeyError: If no ad-hoc record with that id exists.
        """
        await self._ensure_loaded()
        for index, item in enumerate(self._adhoc):
            if item["id"] == kosten_id:
                del self._adhoc[index]
                await self.async_save()
                return
        _LOGGER.error(
            "Cannot remove unknown ad-hoc id=%s (entry=%s)",
            kosten_id,
            self._entry_id,
        )
        raise KeyError(kosten_id)

    async def async_mark_paid(
        self,
        kostenposition_id: str,
        bezahlt_am: date,
    ) -> None:
        """Record a payment timestamp for a kostenposition.

        Idempotent: calling again with a newer date overwrites the previous
        timestamp.

        Args:
            kostenposition_id: The id of the Kostenposition (or AdHoc record)
                that was paid.
            bezahlt_am: The date the payment was made.
        """
        await self._ensure_loaded()
        self._paid[kostenposition_id] = bezahlt_am
        await self.async_save()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        """Load the store lazily if callers skipped :meth:`async_load`."""
        if not self._loaded:
            await self.async_load()

    @staticmethod
    def _parse_date(raw: Any, *, context: str) -> date | None:
        """Parse a value that should be an ISO date string.

        Args:
            raw: The raw value from the persisted payload.
            context: Human-readable location used in log messages.

        Returns:
            The parsed :class:`datetime.date`, or ``None`` if the value is
            missing or malformed (in which case a warning is logged).
        """
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw)
            except ValueError:
                _LOGGER.warning("Invalid ISO date %r at %s", raw, context)
                return None
        _LOGGER.warning("Unexpected non-date value %r at %s", raw, context)
        return None
