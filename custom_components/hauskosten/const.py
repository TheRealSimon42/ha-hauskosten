"""Konstanten fuer die Hauskosten Integration.

Zentrale Stelle fuer alle Strings, Keys und Defaults.
Wird vom integration-architect gepflegt.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hauskosten"

# Schema versioning - erhoehen bei breaking changes am Datenmodell
# v2: Adds Betragsmodus.ABSCHLAG with monatlicher_abschlag_eur /
# abrechnungszeitraum_start / abrechnungszeitraum_dauer_monate on
# Kostenposition subentries.
CONF_SCHEMA_VERSION: Final = 2

# Storage
STORAGE_KEY: Final = f"{DOMAIN}"
STORAGE_VERSION: Final = 1

# Subentry types
SUBENTRY_PARTEI: Final = "partei"
SUBENTRY_KOSTENPOSITION: Final = "kostenposition"

# Config flow keys
CONF_NAME: Final = "name"
CONF_HAUS_NAME: Final = "haus_name"

# Partei keys
CONF_FLAECHE_QM: Final = "flaeche_qm"
CONF_PERSONEN: Final = "personen"
CONF_BEWOHNT_AB: Final = "bewohnt_ab"
CONF_BEWOHNT_BIS: Final = "bewohnt_bis"
CONF_HINWEIS: Final = "hinweis"

# Kostenposition keys
CONF_BEZEICHNUNG: Final = "bezeichnung"
CONF_KATEGORIE: Final = "kategorie"
CONF_ZUORDNUNG: Final = "zuordnung"
CONF_ZUORDNUNG_PARTEI_ID: Final = "zuordnung_partei_id"
CONF_BETRAGSMODUS: Final = "betragsmodus"
CONF_BETRAG_EUR: Final = "betrag_eur"
CONF_PERIODIZITAET: Final = "periodizitaet"
CONF_FAELLIGKEIT: Final = "faelligkeit"
CONF_VERBRAUCHS_ENTITY: Final = "verbrauchs_entity"
CONF_EINHEITSPREIS_EUR: Final = "einheitspreis_eur"
CONF_EINHEIT: Final = "einheit"
CONF_GRUNDGEBUEHR_EUR_MONAT: Final = "grundgebuehr_eur_monat"
CONF_MONATLICHER_ABSCHLAG_EUR: Final = "monatlicher_abschlag_eur"
CONF_ABRECHNUNGSZEITRAUM_START: Final = "abrechnungszeitraum_start"
CONF_ABRECHNUNGSZEITRAUM_DAUER_MONATE: Final = "abrechnungszeitraum_dauer_monate"
CONF_VERTEILUNG: Final = "verteilung"
CONF_VERBRAUCH_ENTITIES_PRO_PARTEI: Final = "verbrauch_entities_pro_partei"
CONF_AKTIV_AB: Final = "aktiv_ab"
CONF_AKTIV_BIS: Final = "aktiv_bis"
CONF_NOTIZ: Final = "notiz"

# Service names
SERVICE_ADD_EINMALIG: Final = "add_einmalig"
SERVICE_MARK_PAID: Final = "mark_paid"
SERVICE_RESET_YEAR: Final = "reset_year"
SERVICE_JAHRESABRECHNUNG_BUCHEN: Final = "jahresabrechnung_buchen"

# Defaults
DEFAULT_UPDATE_INTERVAL_MINUTES: Final = 30
DEFAULT_PERSONEN: Final = 1
DEFAULT_ABRECHNUNGSZEITRAUM_DAUER_MONATE: Final = 12

# Limits (aus docs/DATA_MODEL.md)
MAX_NAME_LENGTH: Final = 50
MAX_FLAECHE_QM: Final = 1000.0
MAX_PERSONEN: Final = 20
