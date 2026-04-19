"""Config flow for the hauskosten integration.

Authoritative spec:
* ``docs/ARCHITECTURE.md`` -- "Config Flow with Subentries" section.
* ``docs/STANDARDS.md``    -- "Config Flow" conventions (Selectors,
  reconfigure-flow, translation key layout).
* ``docs/DATA_MODEL.md``   -- Partei / Kostenposition fields and the
  validation matrix this module enforces at the UI edge.
* ``AGENTS.md``            -- Hard constraints, in particular #7 (all
  user-facing texts via translations) and #5 (no ``device_id``).

The module exposes three distinct flows:

1. :class:`HauskostenConfigFlow` -- the main config flow that creates a
   house (``ConfigEntry``).
2. :class:`ParteiSubentryFlow` -- subentry flow for residential units
   with create + reconfigure steps.
3. :class:`KostenpositionSubentryFlow` -- multi-step subentry flow for
   cost items (basis -> details -> distribution -> sub-meters), again
   with a reconfigure variant that pre-fills all values.

The coordinator consumes the persisted subentry ``data`` dict directly
(see :mod:`.coordinator`), therefore the keys produced here must match
:mod:`.const` ``CONF_*`` constants and :mod:`.models` enum string values.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import selector

from .const import (
    CONF_AKTIV_AB,
    CONF_AKTIV_BIS,
    CONF_BETRAG_EUR,
    CONF_BETRAGSMODUS,
    CONF_BEWOHNT_AB,
    CONF_BEWOHNT_BIS,
    CONF_BEZEICHNUNG,
    CONF_EINHEIT,
    CONF_EINHEITSPREIS_EUR,
    CONF_FAELLIGKEIT,
    CONF_FLAECHE_QM,
    CONF_GRUNDGEBUEHR_EUR_MONAT,
    CONF_HAUS_NAME,
    CONF_HINWEIS,
    CONF_KATEGORIE,
    CONF_NAME,
    CONF_NOTIZ,
    CONF_PERIODIZITAET,
    CONF_PERSONEN,
    CONF_VERBRAUCH_ENTITIES_PRO_PARTEI,
    CONF_VERBRAUCHS_ENTITY,
    CONF_VERTEILUNG,
    CONF_ZUORDNUNG,
    CONF_ZUORDNUNG_PARTEI_ID,
    DEFAULT_PERSONEN,
    DOMAIN,
    MAX_FLAECHE_QM,
    MAX_NAME_LENGTH,
    MAX_PERSONEN,
    SUBENTRY_KOSTENPOSITION,
    SUBENTRY_PARTEI,
)
from .models import (
    Betragsmodus,
    Einheit,
    Kategorie,
    Periodizitaet,
    Verteilung,
    Zuordnung,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

__all__ = [
    "HauskostenConfigFlow",
    "KostenpositionSubentryFlow",
    "ParteiSubentryFlow",
]

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation matrix (docs/DATA_MODEL.md)
# ---------------------------------------------------------------------------

#: Mapping ``(zuordnung, betragsmodus) -> allowed verteilung`` derived from the
#: validation matrix. The config flow uses this to filter the distribution
#: dropdown to valid options and to reject invalid combinations server-side.
_ALLOWED_VERTEILUNGEN: Final[
    dict[tuple[Zuordnung, Betragsmodus], tuple[Verteilung, ...]]
] = {
    (Zuordnung.PARTEI, Betragsmodus.PAUSCHAL): (Verteilung.DIREKT,),
    (Zuordnung.PARTEI, Betragsmodus.VERBRAUCH): (Verteilung.DIREKT,),
    (Zuordnung.HAUS, Betragsmodus.PAUSCHAL): (
        Verteilung.GLEICH,
        Verteilung.FLAECHE,
        Verteilung.PERSONEN,
    ),
    (Zuordnung.HAUS, Betragsmodus.VERBRAUCH): (
        Verteilung.GLEICH,
        Verteilung.FLAECHE,
        Verteilung.PERSONEN,
        Verteilung.VERBRAUCH_SUBZAEHLER,
    ),
}


def _allowed_verteilungen(
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
) -> tuple[Verteilung, ...]:
    """Return the valid ``Verteilung`` values for a ``(zuordnung, betragsmodus)``.

    The empty tuple would indicate a combination outside the matrix; the
    matrix covers all four combinations of the two enums so this function
    never returns an empty tuple in practice.
    """
    return _ALLOWED_VERTEILUNGEN.get((zuordnung, betragsmodus), ())


def _is_valid_combination(
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
    verteilung: Verteilung,
) -> bool:
    """Return True if ``(zuordnung, betragsmodus, verteilung)`` is valid."""
    return verteilung in _allowed_verteilungen(zuordnung, betragsmodus)


# ---------------------------------------------------------------------------
# Shared selectors (instantiated once so every step reuses the same schema)
# ---------------------------------------------------------------------------


def _enum_select(
    values: tuple[str, ...],
    translation_key: str,
    *,
    mode: selector.SelectSelectorMode = selector.SelectSelectorMode.DROPDOWN,
) -> selector.SelectSelector:
    """Build a :class:`SelectSelector` for a StrEnum's values."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(values),
            translation_key=translation_key,
            mode=mode,
        )
    )


_SEL_KATEGORIE = _enum_select(
    tuple(e.value for e in Kategorie),
    translation_key="kategorie",
)
_SEL_ZUORDNUNG = _enum_select(
    tuple(e.value for e in Zuordnung),
    translation_key="zuordnung",
)
_SEL_BETRAGSMODUS = _enum_select(
    tuple(e.value for e in Betragsmodus),
    translation_key="betragsmodus",
)
_SEL_PERIODIZITAET = _enum_select(
    tuple(e.value for e in Periodizitaet),
    translation_key="periodizitaet",
)
_SEL_EINHEIT = _enum_select(
    tuple(e.value for e in Einheit),
    translation_key="einheit",
)


def _verteilung_selector(
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
) -> selector.SelectSelector:
    """Return a :class:`SelectSelector` limited to valid Verteilung values."""
    values = tuple(v.value for v in _allowed_verteilungen(zuordnung, betragsmodus))
    return _enum_select(values, translation_key="verteilung")


_SEL_FLAECHE = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0.01,
        max=MAX_FLAECHE_QM,
        step=0.01,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="m²",
    )
)
_SEL_PERSONEN = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=MAX_PERSONEN,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
    )
)
_SEL_EUR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        step=0.01,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="€",
    )
)
_SEL_DATE = selector.DateSelector()
_SEL_TEXT = selector.TextSelector(selector.TextSelectorConfig())
_SEL_TEXT_MULTILINE = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True),
)
_SEL_ENTITY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor")
)


# ---------------------------------------------------------------------------
# Helpers used across multiple flow classes
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date | None:
    """Best-effort coercion of a Selector value to :class:`datetime.date`.

    ``DateSelector`` submits ISO-8601 strings, but tests may pass already
    instantiated ``date`` objects. Invalid strings produce ``None`` so the
    caller can reject via the dedicated error key.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            _LOGGER.debug("Invalid ISO date submitted: %r", value)
            return None
    return None  # pragma: no cover - defensive


def _normalise_name(value: Any) -> str:
    """Strip and coerce a name-like text input to a ``str``."""
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    """Return a stripped string, or ``None`` if empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_number(value: Any) -> float | None:
    """Return ``float(value)`` or ``None`` if unset."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _existing_parteien(
    entry: ConfigEntry,
    *,
    exclude_subentry_id: str | None = None,
) -> list[ConfigSubentry]:
    """Return all ``partei`` subentries of an entry, optionally excluding one.

    The exclusion is used by the partei reconfigure flow so the currently
    edited subentry does not compare against its own name when validating
    uniqueness.
    """
    return [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_PARTEI
        and sub.subentry_id != exclude_subentry_id
    ]


# ---------------------------------------------------------------------------
# Main config flow
# ---------------------------------------------------------------------------


class HauskostenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow -- create a house entry."""

    VERSION = 1

    @classmethod
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,  # noqa: ARG003 -- required by signature
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Expose the ``partei`` and ``kostenposition`` subentry flows."""
        return {
            SUBENTRY_PARTEI: ParteiSubentryFlow,
            SUBENTRY_KOSTENPOSITION: KostenpositionSubentryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for the house name and create the config entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = _normalise_name(user_input.get(CONF_HAUS_NAME))
            if not name:
                errors[CONF_HAUS_NAME] = "name_required"
            elif len(name) > MAX_NAME_LENGTH:
                errors[CONF_HAUS_NAME] = "name_too_long"
            else:
                await self.async_set_unique_id(name.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_HAUS_NAME: name},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_HAUS_NAME): _SEL_TEXT},
            ),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Partei subentry flow
# ---------------------------------------------------------------------------


class ParteiSubentryFlow(ConfigSubentryFlow):
    """Create or reconfigure a residential unit (Partei).

    The create and reconfigure steps share the same schema and validator,
    only the surrounding control flow differs (``async_create_entry`` vs.
    ``async_update_and_abort``).
    """

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the initial Partei-create step."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = user_input or {}

        if user_input is not None:
            normalised, errors = _validate_partei_input(
                user_input,
                existing=_existing_parteien(entry),
            )
            if not errors:
                return self.async_create_entry(
                    title=normalised[CONF_NAME],
                    data=normalised,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_partei_schema(defaults),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the Partei reconfigure step with prefilled data."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = user_input or subentry.data

        if user_input is not None:
            normalised, errors = _validate_partei_input(
                user_input,
                existing=_existing_parteien(
                    entry,
                    exclude_subentry_id=subentry.subentry_id,
                ),
            )
            if not errors:
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=normalised[CONF_NAME],
                    data=normalised,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_partei_schema(defaults),
            errors=errors,
        )


def _partei_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the :mod:`voluptuous` schema for the Partei form.

    ``defaults`` can be either freshly submitted user input (on error) or
    the existing subentry data (for reconfigure); values are passed through
    untouched so the form redraws exactly what the user saw.
    """

    def _default(key: str, fallback: Any = None) -> Any:
        value = defaults.get(key, fallback) if defaults else fallback
        return value if value not in (None, "") else fallback

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=_default(CONF_NAME, "")): _SEL_TEXT,
            vol.Required(
                CONF_FLAECHE_QM,
                default=_default(CONF_FLAECHE_QM, 50.0),
            ): _SEL_FLAECHE,
            vol.Required(
                CONF_PERSONEN,
                default=_default(CONF_PERSONEN, DEFAULT_PERSONEN),
            ): _SEL_PERSONEN,
            vol.Required(
                CONF_BEWOHNT_AB,
                default=_default(CONF_BEWOHNT_AB, date.today().isoformat()),
            ): _SEL_DATE,
            vol.Optional(
                CONF_BEWOHNT_BIS,
                description={"suggested_value": _default(CONF_BEWOHNT_BIS)},
            ): _SEL_DATE,
            vol.Optional(
                CONF_HINWEIS,
                description={"suggested_value": _default(CONF_HINWEIS)},
            ): _SEL_TEXT,
        }
    )


def _validate_partei_input(
    user_input: Mapping[str, Any],
    *,
    existing: list[ConfigSubentry],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a Partei form submission.

    Returns a tuple ``(normalised_data, errors)``. ``errors`` is empty on
    success and contains keyed ``{field: translation_key}`` entries on
    failure, suitable for returning to ``async_show_form``.
    """
    errors: dict[str, str] = {}
    name = _normalise_name(user_input.get(CONF_NAME))
    if not name:
        errors[CONF_NAME] = "name_required"
    elif len(name) > MAX_NAME_LENGTH:
        errors[CONF_NAME] = "name_too_long"
    elif any(sub.data.get(CONF_NAME) == name for sub in existing):
        errors[CONF_NAME] = "name_not_unique"

    flaeche = _optional_number(user_input.get(CONF_FLAECHE_QM))
    if flaeche is None or flaeche <= 0 or flaeche >= MAX_FLAECHE_QM:
        errors[CONF_FLAECHE_QM] = "invalid_flaeche"

    personen_raw = user_input.get(CONF_PERSONEN)
    try:
        personen = int(personen_raw) if personen_raw is not None else -1
    except (TypeError, ValueError):
        personen = -1
    if personen < 0 or personen > MAX_PERSONEN:
        errors[CONF_PERSONEN] = "invalid_personen"

    bewohnt_ab = _coerce_date(user_input.get(CONF_BEWOHNT_AB))
    if bewohnt_ab is None:
        errors[CONF_BEWOHNT_AB] = "invalid_date_range"
    bewohnt_bis = _coerce_date(user_input.get(CONF_BEWOHNT_BIS))
    if bewohnt_ab is not None and bewohnt_bis is not None and bewohnt_bis < bewohnt_ab:
        errors[CONF_BEWOHNT_BIS] = "invalid_date_range"

    if errors:
        return {}, errors

    assert bewohnt_ab is not None  # noqa: S101 -- guarded above
    assert flaeche is not None  # noqa: S101 -- guarded above

    normalised: dict[str, Any] = {
        CONF_NAME: name,
        CONF_FLAECHE_QM: float(flaeche),
        CONF_PERSONEN: int(personen),
        CONF_BEWOHNT_AB: bewohnt_ab.isoformat(),
        CONF_BEWOHNT_BIS: bewohnt_bis.isoformat() if bewohnt_bis else None,
        CONF_HINWEIS: _optional_text(user_input.get(CONF_HINWEIS)),
    }
    return normalised, {}


# ---------------------------------------------------------------------------
# Kostenposition subentry flow
# ---------------------------------------------------------------------------


class KostenpositionSubentryFlow(ConfigSubentryFlow):
    """Multi-step flow that creates or reconfigures a Kostenposition.

    Steps:

    1. ``user`` / ``reconfigure`` -- collect ``bezeichnung``, ``kategorie``,
       ``zuordnung``, ``betragsmodus``.
    2. ``details`` -- target party (if PARTEI), pauschal fields or verbrauch
       fields (depending on ``betragsmodus``).
    3. ``verteilung`` -- distribution key (filtered by the matrix) and the
       optional active date range.
    4. ``subzaehler`` -- appears only when ``verteilung`` is
       ``VERBRAUCH_SUBZAEHLER``; collects one entity per party.

    The final persisted ``data`` dict mirrors :class:`models.Kostenposition`.
    """

    def __init__(self) -> None:
        """Initialise per-flow state."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._reconfigure_mode: bool = False

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the initial user step (Kostenposition creation)."""
        entry = self._get_entry()
        if not _existing_parteien(entry):
            return self.async_abort(reason="no_parteien")
        return await self._handle_basis_step(
            user_input,
            step_id="user",
            defaults=user_input or {},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the initial reconfigure step."""
        self._reconfigure_mode = True
        entry = self._get_entry()
        if not _existing_parteien(entry):
            return self.async_abort(reason="no_parteien")
        subentry = self._get_reconfigure_subentry()
        defaults: Mapping[str, Any] = user_input or subentry.data
        return await self._handle_basis_step(
            user_input,
            step_id="reconfigure",
            defaults=defaults,
        )

    # ------------------------------------------------------------------
    # Step 1 -- basis (bezeichnung / kategorie / zuordnung / betragsmodus)
    # ------------------------------------------------------------------

    async def _handle_basis_step(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        defaults: Mapping[str, Any],
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            bezeichnung = _normalise_name(user_input.get(CONF_BEZEICHNUNG))
            if not bezeichnung:
                errors[CONF_BEZEICHNUNG] = "bezeichnung_required"
            elif len(bezeichnung) > MAX_NAME_LENGTH:
                errors[CONF_BEZEICHNUNG] = "bezeichnung_too_long"

            if not errors:
                self._data[CONF_BEZEICHNUNG] = bezeichnung
                self._data[CONF_KATEGORIE] = user_input[CONF_KATEGORIE]
                self._data[CONF_ZUORDNUNG] = user_input[CONF_ZUORDNUNG]
                self._data[CONF_BETRAGSMODUS] = user_input[CONF_BETRAGSMODUS]
                return await self.async_step_details()

        return self.async_show_form(
            step_id=step_id,
            data_schema=_basis_schema(defaults),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2 -- details
    # ------------------------------------------------------------------

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect details driven by zuordnung / betragsmodus."""
        zuordnung = Zuordnung(self._data[CONF_ZUORDNUNG])
        betragsmodus = Betragsmodus(self._data[CONF_BETRAGSMODUS])
        entry = self._get_entry()
        parteien = _existing_parteien(entry)
        defaults: Mapping[str, Any] = user_input or self._reconfigure_defaults()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_details_input(
                user_input,
                zuordnung=zuordnung,
                betragsmodus=betragsmodus,
                parteien=parteien,
            )
            if not errors:
                self._store_details(
                    user_input,
                    zuordnung=zuordnung,
                    betragsmodus=betragsmodus,
                )
                return await self.async_step_verteilung()

        return self.async_show_form(
            step_id="details",
            data_schema=_details_schema(
                zuordnung=zuordnung,
                betragsmodus=betragsmodus,
                parteien=parteien,
                defaults=defaults,
            ),
            errors=errors,
        )

    def _store_details(
        self,
        user_input: Mapping[str, Any],
        *,
        zuordnung: Zuordnung,
        betragsmodus: Betragsmodus,
    ) -> None:
        """Copy the validated details-step fields into ``self._data``."""
        if zuordnung is Zuordnung.PARTEI:
            self._data[CONF_ZUORDNUNG_PARTEI_ID] = user_input[CONF_ZUORDNUNG_PARTEI_ID]
        else:
            self._data[CONF_ZUORDNUNG_PARTEI_ID] = None

        if betragsmodus is Betragsmodus.PAUSCHAL:
            self._data[CONF_BETRAG_EUR] = float(user_input[CONF_BETRAG_EUR])
            self._data[CONF_PERIODIZITAET] = user_input[CONF_PERIODIZITAET]
            faelligkeit = _coerce_date(user_input[CONF_FAELLIGKEIT])
            self._data[CONF_FAELLIGKEIT] = (
                faelligkeit.isoformat() if faelligkeit else None
            )
            self._data[CONF_VERBRAUCHS_ENTITY] = None
            self._data[CONF_EINHEITSPREIS_EUR] = None
            self._data[CONF_EINHEIT] = None
            self._data[CONF_GRUNDGEBUEHR_EUR_MONAT] = None
        else:
            self._data[CONF_BETRAG_EUR] = None
            self._data[CONF_PERIODIZITAET] = None
            self._data[CONF_FAELLIGKEIT] = None
            self._data[CONF_VERBRAUCHS_ENTITY] = user_input[CONF_VERBRAUCHS_ENTITY]
            self._data[CONF_EINHEITSPREIS_EUR] = float(
                user_input[CONF_EINHEITSPREIS_EUR]
            )
            self._data[CONF_EINHEIT] = user_input[CONF_EINHEIT]
            self._data[CONF_GRUNDGEBUEHR_EUR_MONAT] = _optional_number(
                user_input.get(CONF_GRUNDGEBUEHR_EUR_MONAT)
            )

    # ------------------------------------------------------------------
    # Step 3 -- verteilung
    # ------------------------------------------------------------------

    async def async_step_verteilung(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect distribution key and optional active range."""
        zuordnung = Zuordnung(self._data[CONF_ZUORDNUNG])
        betragsmodus = Betragsmodus(self._data[CONF_BETRAGSMODUS])
        defaults: Mapping[str, Any] = user_input or self._reconfigure_defaults()

        errors: dict[str, str] = {}
        if user_input is not None:
            verteilung_raw = user_input.get(CONF_VERTEILUNG)
            if not verteilung_raw:
                errors[CONF_VERTEILUNG] = "verteilung_required"
            else:
                try:
                    verteilung = Verteilung(verteilung_raw)
                except ValueError:
                    errors[CONF_VERTEILUNG] = "invalid_combination"
                else:
                    if not _is_valid_combination(zuordnung, betragsmodus, verteilung):
                        errors[CONF_VERTEILUNG] = "invalid_combination"
                    else:
                        aktiv_ab = _coerce_date(user_input.get(CONF_AKTIV_AB))
                        aktiv_bis = _coerce_date(user_input.get(CONF_AKTIV_BIS))
                        if (
                            aktiv_ab is not None
                            and aktiv_bis is not None
                            and aktiv_bis < aktiv_ab
                        ):
                            errors[CONF_AKTIV_BIS] = "invalid_date_range"
                        else:
                            self._data[CONF_VERTEILUNG] = verteilung.value
                            self._data[CONF_AKTIV_AB] = (
                                aktiv_ab.isoformat() if aktiv_ab else None
                            )
                            self._data[CONF_AKTIV_BIS] = (
                                aktiv_bis.isoformat() if aktiv_bis else None
                            )
                            self._data[CONF_NOTIZ] = _optional_text(
                                user_input.get(CONF_NOTIZ)
                            )
                            if verteilung is Verteilung.VERBRAUCH_SUBZAEHLER:
                                return await self.async_step_subzaehler()
                            return self._finalise()

        return self.async_show_form(
            step_id="verteilung",
            data_schema=_verteilung_schema(
                zuordnung=zuordnung,
                betragsmodus=betragsmodus,
                defaults=defaults,
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 4 -- subzaehler (only for VERBRAUCH_SUBZAEHLER)
    # ------------------------------------------------------------------

    async def async_step_subzaehler(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect one sub-meter entity per party."""
        entry = self._get_entry()
        parteien = _existing_parteien(entry)
        defaults_sub: dict[str, Any] = {}
        existing_map: dict[str, Any] = {}
        if self._reconfigure_mode:
            existing_map = dict(
                self._get_reconfigure_subentry().data.get(
                    CONF_VERBRAUCH_ENTITIES_PRO_PARTEI, {}
                )
                or {}
            )
        for sub in parteien:
            key = f"entity_{sub.subentry_id}"
            if user_input is not None:
                defaults_sub[key] = user_input.get(key, "")
            else:
                defaults_sub[key] = existing_map.get(sub.subentry_id, "")

        errors: dict[str, str] = {}
        if user_input is not None:
            mapping: dict[str, str] = {}
            for sub in parteien:
                key = f"entity_{sub.subentry_id}"
                entity_id = user_input.get(key)
                if not entity_id:
                    errors[key] = "subzaehler_missing"
                else:
                    mapping[sub.subentry_id] = str(entity_id)
            if not errors:
                self._data[CONF_VERBRAUCH_ENTITIES_PRO_PARTEI] = mapping
                return self._finalise()

        return self.async_show_form(
            step_id="subzaehler",
            data_schema=_subzaehler_schema(parteien, defaults_sub),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def _finalise(self) -> SubentryFlowResult:
        """Persist the collected data as a new or updated subentry."""
        self._data.setdefault(CONF_VERBRAUCH_ENTITIES_PRO_PARTEI, None)
        self._data.setdefault(CONF_ZUORDNUNG_PARTEI_ID, None)
        self._data.setdefault(CONF_NOTIZ, None)
        title = self._data[CONF_BEZEICHNUNG]
        if self._reconfigure_mode:
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            return self.async_update_and_abort(
                entry,
                subentry,
                title=title,
                data=self._data,
            )
        return self.async_create_entry(title=title, data=self._data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reconfigure_defaults(self) -> Mapping[str, Any]:
        """Return the pre-fill map for follow-up steps during reconfigure."""
        if not self._reconfigure_mode:
            return {}
        return self._get_reconfigure_subentry().data


# ---------------------------------------------------------------------------
# Schema builders (module-level so they are unit-testable)
# ---------------------------------------------------------------------------


def _basis_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the schema for the Kostenposition basis step."""

    def _default(key: str, fallback: Any) -> Any:
        value = defaults.get(key, fallback) if defaults else fallback
        return value if value not in (None, "") else fallback

    return vol.Schema(
        {
            vol.Required(
                CONF_BEZEICHNUNG,
                default=_default(CONF_BEZEICHNUNG, ""),
            ): _SEL_TEXT,
            vol.Required(
                CONF_KATEGORIE,
                default=_default(CONF_KATEGORIE, Kategorie.SONSTIGES.value),
            ): _SEL_KATEGORIE,
            vol.Required(
                CONF_ZUORDNUNG,
                default=_default(CONF_ZUORDNUNG, Zuordnung.HAUS.value),
            ): _SEL_ZUORDNUNG,
            vol.Required(
                CONF_BETRAGSMODUS,
                default=_default(CONF_BETRAGSMODUS, Betragsmodus.PAUSCHAL.value),
            ): _SEL_BETRAGSMODUS,
        }
    )


def _details_schema(
    *,
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
    parteien: list[ConfigSubentry],
    defaults: Mapping[str, Any],
) -> vol.Schema:
    """Build the schema for the Kostenposition details step."""

    def _default(key: str, fallback: Any = None) -> Any:
        if not defaults:
            return fallback
        value = defaults.get(key, fallback)
        return value if value not in (None, "") else fallback

    schema: dict[Any, Any] = {}
    if zuordnung is Zuordnung.PARTEI:
        partei_options = [
            selector.SelectOptionDict(
                value=sub.subentry_id,
                label=str(sub.data.get(CONF_NAME, sub.title)),
            )
            for sub in parteien
        ]
        schema[
            vol.Required(
                CONF_ZUORDNUNG_PARTEI_ID,
                default=_default(CONF_ZUORDNUNG_PARTEI_ID, ""),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=partei_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    if betragsmodus is Betragsmodus.PAUSCHAL:
        schema[
            vol.Required(CONF_BETRAG_EUR, default=_default(CONF_BETRAG_EUR, 0.0))
        ] = _SEL_EUR
        schema[
            vol.Required(
                CONF_PERIODIZITAET,
                default=_default(CONF_PERIODIZITAET, Periodizitaet.JAEHRLICH.value),
            )
        ] = _SEL_PERIODIZITAET
        schema[
            vol.Required(
                CONF_FAELLIGKEIT,
                default=_default(CONF_FAELLIGKEIT, date.today().isoformat()),
            )
        ] = _SEL_DATE
    else:
        schema[
            vol.Required(
                CONF_VERBRAUCHS_ENTITY,
                default=_default(CONF_VERBRAUCHS_ENTITY, ""),
            )
        ] = _SEL_ENTITY_SENSOR
        schema[
            vol.Required(
                CONF_EINHEITSPREIS_EUR,
                default=_default(CONF_EINHEITSPREIS_EUR, 0.0),
            )
        ] = _SEL_EUR
        schema[
            vol.Required(
                CONF_EINHEIT,
                default=_default(CONF_EINHEIT, Einheit.KWH.value),
            )
        ] = _SEL_EINHEIT
        schema[
            vol.Optional(
                CONF_GRUNDGEBUEHR_EUR_MONAT,
                description={"suggested_value": _default(CONF_GRUNDGEBUEHR_EUR_MONAT)},
            )
        ] = _SEL_EUR

    return vol.Schema(schema)


def _validate_details_input(
    user_input: Mapping[str, Any],
    *,
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
    parteien: list[ConfigSubentry],
) -> dict[str, str]:
    """Validate the details step input; return field -> error-key map."""
    errors: dict[str, str] = {}
    if zuordnung is Zuordnung.PARTEI:
        partei_id = user_input.get(CONF_ZUORDNUNG_PARTEI_ID)
        valid_ids = {sub.subentry_id for sub in parteien}
        if not partei_id or partei_id not in valid_ids:
            errors[CONF_ZUORDNUNG_PARTEI_ID] = "partei_required"

    if betragsmodus is Betragsmodus.PAUSCHAL:
        betrag = _optional_number(user_input.get(CONF_BETRAG_EUR))
        if betrag is None or betrag < 0:
            errors[CONF_BETRAG_EUR] = "betrag_required"
        if not user_input.get(CONF_PERIODIZITAET):
            errors[CONF_PERIODIZITAET] = "periodizitaet_required"
        if _coerce_date(user_input.get(CONF_FAELLIGKEIT)) is None:
            errors[CONF_FAELLIGKEIT] = "faelligkeit_required"
    else:
        if not user_input.get(CONF_VERBRAUCHS_ENTITY):
            errors[CONF_VERBRAUCHS_ENTITY] = "verbrauchs_entity_required"
        preis = _optional_number(user_input.get(CONF_EINHEITSPREIS_EUR))
        if preis is None or preis < 0:
            errors[CONF_EINHEITSPREIS_EUR] = "einheitspreis_required"
        if not user_input.get(CONF_EINHEIT):
            errors[CONF_EINHEIT] = "einheit_required"

    return errors


def _verteilung_schema(
    *,
    zuordnung: Zuordnung,
    betragsmodus: Betragsmodus,
    defaults: Mapping[str, Any],
) -> vol.Schema:
    """Build the schema for the distribution step."""

    def _default(key: str, fallback: Any = None) -> Any:
        if not defaults:
            return fallback
        value = defaults.get(key, fallback)
        return value if value not in (None, "") else fallback

    allowed = _allowed_verteilungen(zuordnung, betragsmodus)
    fallback_value = allowed[0].value if allowed else ""
    default_verteilung = _default(CONF_VERTEILUNG, fallback_value)
    if default_verteilung not in {v.value for v in allowed}:
        default_verteilung = fallback_value

    return vol.Schema(
        {
            vol.Required(
                CONF_VERTEILUNG,
                default=default_verteilung,
            ): _verteilung_selector(zuordnung, betragsmodus),
            vol.Optional(
                CONF_AKTIV_AB,
                description={"suggested_value": _default(CONF_AKTIV_AB)},
            ): _SEL_DATE,
            vol.Optional(
                CONF_AKTIV_BIS,
                description={"suggested_value": _default(CONF_AKTIV_BIS)},
            ): _SEL_DATE,
            vol.Optional(
                CONF_NOTIZ,
                description={"suggested_value": _default(CONF_NOTIZ)},
            ): _SEL_TEXT_MULTILINE,
        }
    )


def _subzaehler_schema(
    parteien: list[ConfigSubentry],
    defaults: Mapping[str, Any],
) -> vol.Schema:
    """Build the schema for the sub-meter step (one entity per party)."""
    schema: dict[Any, Any] = {}
    for sub in parteien:
        key = f"entity_{sub.subentry_id}"
        default_value = defaults.get(key, "") if defaults else ""
        schema[
            vol.Required(
                key,
                default=default_value if default_value else "",
            )
        ] = _SEL_ENTITY_SENSOR
    return vol.Schema(schema)
