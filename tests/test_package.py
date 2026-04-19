"""Smoke test: package imports cleanly.

Placeholder until phase 1 test modules land. Can be removed once real
distribution/calculations tests exist.
"""

from __future__ import annotations


def test_package_imports() -> None:
    """The hauskosten package module imports without side-effects."""
    import custom_components.hauskosten as pkg

    assert pkg.PLATFORMS == ["sensor"]


def test_constants_defined() -> None:
    """Core constants are exposed as expected by docs/DATA_MODEL.md."""
    from custom_components.hauskosten import const

    assert const.DOMAIN == "hauskosten"
    assert const.CONF_SCHEMA_VERSION >= 1
    assert const.SUBENTRY_PARTEI == "partei"
    assert const.SUBENTRY_KOSTENPOSITION == "kostenposition"
