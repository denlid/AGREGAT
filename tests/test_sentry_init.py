"""Sprint 4.6 — Tests for engine.observability.sentry."""

from __future__ import annotations

import pytest

from engine.observability import sentry as sentry_mod


@pytest.fixture(autouse=True)
def _reset_sentry_state() -> None:
    """Each test starts with an uninitialised Sentry."""
    sentry_mod._SENTRY_INITIALISED = False
    sentry_mod._SENTRY_DSN = None
    yield
    sentry_mod._SENTRY_INITIALISED = False
    sentry_mod._SENTRY_DSN = None


def test_no_dsn_skips_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGREGAT_SENTRY_DSN", raising=False)
    status = sentry_mod.init_sentry()
    assert status.enabled is False
    assert status.dsn_present is False
    assert "not set" in status.reason
    assert sentry_mod.is_sentry_enabled() is False


def test_dsn_without_sdk_disables_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGREGAT_SENTRY_DSN", "https://abc@example.io/1")
    monkeypatch.setattr(sentry_mod, "_has_sdk", lambda: False)
    status = sentry_mod.init_sentry()
    assert status.enabled is False
    assert status.dsn_present is True
    assert status.sdk_installed is False


def test_dsn_with_sdk_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even though the real SDK probably isn't installed in CI, we
    monkeypatch :func:`_has_sdk` and ``sentry_sdk.init`` so the
    branch coverage holds."""
    monkeypatch.setenv("AGREGAT_SENTRY_DSN", "https://abc@example.io/1")
    monkeypatch.setattr(sentry_mod, "_has_sdk", lambda: True)
    fake_sdk = type("M", (), {"init": staticmethod(lambda **kw: None)})()
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake_sdk)
    status = sentry_mod.init_sentry(environment="ci")
    assert status.enabled is True
    assert status.reason == "enabled"
    assert sentry_mod.is_sentry_enabled() is True


def test_status_reflects_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGREGAT_SENTRY_DSN", raising=False)
    s = sentry_mod.sentry_status()
    assert s.enabled is False


def test_explicit_dsn_argument_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGREGAT_SENTRY_DSN", raising=False)
    monkeypatch.setattr(sentry_mod, "_has_sdk", lambda: False)
    status = sentry_mod.init_sentry(dsn="https://abc@example.io/1")
    assert status.dsn_present is True
    assert status.sdk_installed is False
