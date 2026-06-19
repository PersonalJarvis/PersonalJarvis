"""Contract-Tests fuer die AdminClient-Schnittstelle.

Parallel zum Muster aus ``test_harness_protocol.py``: wir pruefen, dass
die AdminClient-Klasse die gewohnten Methoden hat (``execute`` ist
async, DestructiveRequiresApproval existiert, Events werden publiziert).

Das hier ist *kein* runtime_checkable Protocol, weil ``AdminClient``
strikter ist als ein freies Protocol — aber die Form ist so stabil,
dass Regressions-Tests sinnvoll sind.
"""
from __future__ import annotations

import inspect

import pytest

from jarvis.admin.client import (
    ADMIN_HMAC_ENV,
    ADMIN_HMAC_KEY,
    AdminClient,
    DestructiveRequiresApproval,
    admin_secret_configured,
)
from jarvis.admin.schema import AdminOperation, AdminResponse


def test_admin_client_class_exists():
    assert inspect.isclass(AdminClient)


def test_admin_client_execute_is_async():
    assert inspect.iscoroutinefunction(AdminClient.execute)


def test_admin_client_constructor_accepts_bus_and_token():
    sig = inspect.signature(AdminClient.__init__)
    params = sig.parameters
    assert "bus" in params
    assert "cancel_token" in params
    # Pipe-Client-Injection fuer Tests muss moeglich sein.
    assert "pipe_client" in params or "pipe_name" in params


def test_destructive_requires_approval_is_exception():
    assert issubclass(DestructiveRequiresApproval, Exception)


def test_destructive_exception_carries_op_metadata():
    from jarvis.admin.schema import UninstallWingetOp
    op = UninstallWingetOp(package_id="7zip.7zip")
    exc = DestructiveRequiresApproval(op)
    assert exc.op_id == str(op.op_id)
    assert exc.op_type == "uninstall_winget"


def test_admin_hmac_env_and_key_constants_stable():
    """Wenn diese Konstanten geaendert werden, muessen Wizard + Helper + ADR
    mitgezogen werden — deshalb ist das ein Contract.
    """
    assert ADMIN_HMAC_KEY == "jarvis_admin_hmac"
    assert ADMIN_HMAC_ENV == "JARVIS_ADMIN_HMAC"


def test_admin_secret_configured_callable():
    assert callable(admin_secret_configured)
    # Idempotent: Aufruf darf nicht crashen.
    _ = admin_secret_configured()


@pytest.mark.asyncio
async def test_admin_client_returns_adminresponse_on_no_secret(monkeypatch):
    """Ohne hinterlegtes Secret: ``execute`` liefert einen AdminResponse
    mit ``error_code=no_secret`` zurueck, keine Exception."""
    from jarvis.admin import client as client_mod

    def _no_secret(*_args, **_kwargs):
        return None

    monkeypatch.setattr(client_mod, "get_secret", _no_secret)
    from jarvis.admin.schema import InstallWingetOp
    c = AdminClient()
    resp = await c.execute(InstallWingetOp(package_id="7zip.7zip"))
    assert isinstance(resp, AdminResponse)
    assert resp.success is False
    assert resp.error_code == "no_secret"


def test_admin_operation_and_response_importable_from_admin():
    """Sanity: die zentrale __init__-Exports existieren."""
    from jarvis import admin

    assert hasattr(admin, "AdminOperation")
    assert hasattr(admin, "AdminResponse")
    assert admin.AdminOperation is AdminOperation
    assert admin.AdminResponse is AdminResponse
