"""REST API for the CLI integration (desktop UI).

Endpoints:
- ``GET  /api/clis``                     → list of all catalog entries + status
- ``GET  /api/clis/{name}``              → full detail record
- ``POST /api/clis/{name}/check``        → live binary/auth probe
- ``POST /api/clis/{name}/install``      → start install job (streams via WS)
- ``DELETE /api/clis/{name}/install/{job_id}`` → cancel install
- ``POST /api/clis/{name}/connect``      → start auth flow (OAuth/API key)
- ``POST /api/clis/{name}/disconnect``   → remove auth
- ``GET  /api/clis/{name}/usage``        → paginated usage history
- ``GET  /api/clis/{name}/usage/stats``  → stats aggregation
- ``POST /api/clis/custom``              → register a custom CLI
- ``DELETE /api/clis/custom/{name}``     → remove a custom CLI
- ``DELETE /api/clis/{name}/usage``      → delete history
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.spec import CliSpec, CliStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clis", tags=["clis"])

# Test-Hub guardrails: the test-run endpoint is NOT on the voice critical path,
# so a generous wall-clock cap is fine. Caps the whole brain turn (tool choice +
# CLI subprocess) so a hanging CLI cannot wedge the endpoint.
_TEST_RUN_TIMEOUT_S = 90.0


def _require_registry(request: Request) -> CliToolRegistry:
    reg = getattr(request.app.state, "cli_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="CliToolRegistry not available")
    return reg


CliStatusStr = Literal["connected", "disconnected", "not_installed", "error", "checking"]


class CliSummary(BaseModel):
    name: str
    display_name: str
    category: str
    icon: str
    description: str
    status: CliStatusStr
    installed: bool
    connected: bool
    version: str | None
    auth_mode: str
    is_custom: bool
    last_used_at: int | None
    usage_count_7d: int
    error: str | None = None


class InstallMethodInfo(BaseModel):
    manager: str
    command: str
    requires_admin: bool = False
    notes: str | None = None


class SecretKeyInfo(BaseModel):
    name: str
    env_var: str
    required: bool = True


class CliDetail(CliSummary):
    homepage: str
    binary_name: str
    binary_path: str | None
    install_methods: list[InstallMethodInfo]
    recommended_install: str | None
    secret_keys: list[SecretKeyInfo]
    secrets_set: dict[str, bool]
    login_command: str | None
    logout_command: str | None
    status_command: str | None
    check_command: str
    tool_schema_examples: list[str]
    risk_tier: str
    allow_patterns: list[str]
    deny_patterns: list[str]


class ListClisResponse(BaseModel):
    clis: list[CliSummary]
    total: int
    connected: int
    installed: int
    categories: list[str]


class CheckResponse(BaseModel):
    name: str
    status: CliStatusStr
    installed: bool
    connected: bool
    version: str | None
    binary_path: str | None
    error: str | None = None


class UsageEntry(BaseModel):
    id: int
    trace_id: str | None
    full_command: str
    exit_code: int | None
    stdout_len: int
    stderr_len: int
    stderr_preview: str | None
    duration_ms: int | None
    caller: str
    started_at: int
    finished_at: int | None


class UsageListResponse(BaseModel):
    entries: list[UsageEntry]
    total: int
    page: int
    page_size: int


class UsageStatsResponse(BaseModel):
    total_calls: int
    success_calls: int
    success_rate: float
    avg_duration_ms: int
    last_used_at: int | None
    top_commands: list[tuple[str, int]]
    calls_by_caller: dict[str, int]


class InstallRequest(BaseModel):
    method: str


class InstallStartResponse(BaseModel):
    ok: bool
    job_id: str
    command: str
    error: str | None = None


class ConnectRequest(BaseModel):
    mode: Literal["oauth_cli", "api_key"]
    secrets: dict[str, str] | None = None
    validate_creds: bool = True


class ConnectResponse(BaseModel):
    ok: bool
    status: CliStatusStr
    job_id: str | None = None
    error: str | None = None


class DisconnectResponse(BaseModel):
    ok: bool


class SpawnExternalRequest(BaseModel):
    """Request to /spawn-external — opens a **real** Windows terminal
    (wt/pwsh) and runs either the install or the login command for the
    given CLI in it. Unlike the embedded xterm in the frontend, the
    terminal runs as a detached subprocess in the user session
    (cwd = USERPROFILE), separate from the Jarvis app.
    """

    kind: Literal["install", "login"]
    method: str | None = None  # only for kind=install: "winget"|"scoop"|"npm"|...


class SpawnExternalResponse(BaseModel):
    ok: bool
    method: str = "failed"  # "wt" | "pwsh" | "powershell" | "failed"
    command: str | None = None
    error: str | None = None
    error: str | None = None


class CustomCliPayload(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")
    display_name: str
    description: str = ""
    homepage: str = ""
    binary_name: str
    check_command: list[str] = Field(min_length=1)
    version_parse_regex: str = r"(\S+)"
    install_manual_url: str = ""
    auth_mode: Literal["oauth_cli", "api_key", "config_file", "none"] = "none"
    login_command: list[str] | None = None
    logout_command: list[str] | None = None
    status_command: list[str] = Field(default_factory=list)
    status_parse: str = "text_nonempty"
    secret_keys: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    risk_tier: Literal["safe", "monitor", "ask", "block"] = "monitor"
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    category: str = "other"
    icon: str = ""


class TestRunRequest(BaseModel):
    """A natural-language instruction for the CLI Test Hub.

    ``cli_hint`` is an optional soft hint (e.g. ``"gcloud"``); the brain still
    chooses the tool, but the hint is appended to the instruction so the brain
    is nudged toward the right ``cli_<name>`` tool.
    """

    instruction: str = Field(min_length=1, max_length=2000)
    cli_hint: str | None = Field(default=None, max_length=60)


class TestRunStep(BaseModel):
    tool: str
    command: str
    exit_code: int | None = None


class TestRunResponse(BaseModel):
    ok: bool
    instruction: str
    tool_called: str | None = None
    command: str | None = None
    risk_tier: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    summary: str = ""
    error: str | None = None
    steps: list[TestRunStep] = Field(default_factory=list)


_WEEK_MS = 7 * 24 * 60 * 60 * 1000


def _status_string(spec: CliSpec, status: CliStatus | None) -> CliStatusStr:
    if status is None:
        return "checking"
    if status.error:
        return "error"
    if not status.installed:
        return "not_installed"
    if spec.auth.type in ("none", "config_file"):
        return "connected"
    return "connected" if status.auth_status == "connected" else "disconnected"


def _summary_for(spec: CliSpec, status: CliStatus | None, usage_count_7d: int) -> CliSummary:
    status_str = _status_string(spec, status)
    return CliSummary(
        name=spec.name,
        display_name=spec.display_name,
        category=spec.category,
        icon=spec.icon,
        description=spec.description,
        status=status_str,
        installed=bool(status and status.installed),
        connected=status_str == "connected",
        version=(status.version if status else None),
        auth_mode=spec.auth.type,
        is_custom=spec.source == "custom",
        last_used_at=(status.last_used_at if status else None),
        usage_count_7d=usage_count_7d,
        error=(status.error if status else None),
    )


def _install_methods_of(spec: CliSpec) -> tuple[list[InstallMethodInfo], str | None]:
    methods: list[InstallMethodInfo] = []
    i = spec.install
    if i.winget_id:
        methods.append(
            InstallMethodInfo(
                manager="winget",
                command=f"winget install --id {i.winget_id} -e --silent",
            )
        )
    if i.scoop_package:
        methods.append(
            InstallMethodInfo(
                manager="scoop",
                command=f"scoop install {i.scoop_package}",
            )
        )
    if i.npm_package:
        methods.append(
            InstallMethodInfo(
                manager="npm",
                command=f"npm install -g {i.npm_package}",
            )
        )
    if i.pip_package:
        methods.append(
            InstallMethodInfo(
                manager="pip",
                command=f"pip install --upgrade {i.pip_package}",
            )
        )
    if i.cargo_package:
        methods.append(
            InstallMethodInfo(
                manager="cargo",
                command=f"cargo install {i.cargo_package}",
            )
        )
    if i.script_url:
        methods.append(
            InstallMethodInfo(
                manager="script",
                command=i.script_url,
            )
        )
    if not methods and i.manual_url:
        methods.append(
            InstallMethodInfo(
                manager="manual",
                command=i.manual_url,
            )
        )
    recommended = methods[0].manager if methods else None
    return methods, recommended


def _secret_keys_info(spec: CliSpec) -> list[SecretKeyInfo]:
    return [
        SecretKeyInfo(name=key, env_var=env_var, required=True)
        for key, env_var in zip(spec.auth.secret_keys, spec.auth.env_vars, strict=False)
    ]


def _which_secrets_are_set(spec: CliSpec) -> dict[str, bool]:
    from jarvis.core.config import get_secret

    out: dict[str, bool] = {}
    for key in spec.auth.secret_keys:
        try:
            out[key] = bool(get_secret(key))
        except Exception:  # noqa: BLE001
            out[key] = False
    return out


def _publish_safe(bus: Any, event: Any) -> None:
    try:
        if asyncio.iscoroutinefunction(bus.publish):
            asyncio.create_task(bus.publish(event))
        else:
            bus.publish(event)
    except Exception as exc:  # noqa: BLE001
        log.debug("bus.publish failed: %s", exc)


@router.get("", response_model=ListClisResponse)
async def list_clis(request: Request) -> ListClisResponse:
    import time

    reg = _require_registry(request)
    since_ms = int(time.time() * 1000) - _WEEK_MS
    usage = reg.usage_log()

    catalog = reg.catalog().all()
    status_map = reg.all_status()
    summaries: list[CliSummary] = []
    categories: set[str] = set()

    for name, spec in catalog.items():
        status = status_map.get(name)
        count = usage.count_for(name, since_ms=since_ms)
        last_used = usage.last_used_at(name)
        if status and last_used is not None:
            status.last_used_at = last_used
            status.usage_count_7d = count
        summaries.append(_summary_for(spec, status, count))
        categories.add(spec.category)

    summaries.sort(key=lambda s: (s.category, s.display_name.lower()))
    return ListClisResponse(
        clis=summaries,
        total=len(summaries),
        connected=sum(1 for s in summaries if s.status == "connected"),
        installed=sum(1 for s in summaries if s.installed),
        categories=sorted(categories),
    )


@router.get("/{name}", response_model=CliDetail)
async def get_cli(name: str, request: Request) -> CliDetail:
    import time

    reg = _require_registry(request)
    spec = reg.catalog().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    status = reg.status_of(name)
    usage = reg.usage_log()
    count_7d = usage.count_for(name, since_ms=int(time.time() * 1000) - _WEEK_MS)
    last_used = usage.last_used_at(name)
    if status and last_used is not None:
        status.last_used_at = last_used
        status.usage_count_7d = count_7d

    summary = _summary_for(spec, status, count_7d)
    methods, recommended = _install_methods_of(spec)
    secrets_set = _which_secrets_are_set(spec)

    return CliDetail(
        **summary.model_dump(),
        homepage=spec.homepage,
        binary_name=spec.binary_name,
        binary_path=(status.binary_path if status else None),
        install_methods=methods,
        recommended_install=recommended,
        secret_keys=_secret_keys_info(spec),
        secrets_set=secrets_set,
        login_command=(" ".join(spec.auth.login_command) if spec.auth.login_command else None),
        logout_command=(" ".join(spec.auth.logout_command) if spec.auth.logout_command else None),
        status_command=(" ".join(spec.auth.status_command) if spec.auth.status_command else None),
        check_command=" ".join(spec.check_command),
        tool_schema_examples=list(spec.tool_schema_examples),
        risk_tier=spec.risk.default_tier,
        allow_patterns=list(spec.risk.whitelist_patterns),
        deny_patterns=list(spec.risk.blacklist_patterns),
    )


@router.post("/{name}/check", response_model=CheckResponse)
async def check_cli(name: str, request: Request) -> CheckResponse:
    reg = _require_registry(request)
    if reg.catalog().get(name) is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    status = await reg.refresh_status(name)
    if status is None:
        raise HTTPException(status_code=500, detail="refresh failed")
    spec = reg.catalog().get(name)
    assert spec is not None
    return CheckResponse(
        name=name,
        status=_status_string(spec, status),
        installed=status.installed,
        connected=status.auth_status == "connected"
        or spec.auth.type in ("none", "config_file")
        and status.installed,
        version=status.version,
        binary_path=status.binary_path,
        error=status.error,
    )


@router.get("/{name}/usage", response_model=UsageListResponse)
async def list_usage(
    name: str,
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
    since_ms: int | None = Query(None, ge=0),
    until_ms: int | None = Query(None, ge=0),
    success_only: bool = False,
    search: str | None = Query(None, max_length=200),
) -> UsageListResponse:
    reg = _require_registry(request)
    if reg.catalog().get(name) is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    usage = reg.usage_log()
    offset = (page - 1) * page_size
    rows = usage.list_for(
        name,
        limit=page_size,
        offset=offset,
        since_ms=since_ms,
        until_ms=until_ms,
        success_only=success_only,
        search=search,
    )
    total = usage.count_for(name)
    return UsageListResponse(
        entries=[
            UsageEntry(
                id=r.id,
                trace_id=r.trace_id,
                full_command=r.full_command,
                exit_code=r.exit_code,
                stdout_len=r.stdout_len,
                stderr_len=r.stderr_len,
                stderr_preview=r.stderr_preview,
                duration_ms=r.duration_ms,
                caller=r.caller,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{name}/usage/stats", response_model=UsageStatsResponse)
async def usage_stats(
    name: str, request: Request, since_ms: int | None = Query(None, ge=0)
) -> UsageStatsResponse:
    reg = _require_registry(request)
    if reg.catalog().get(name) is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    stats = reg.usage_log().stats_for(name, since_ms=since_ms)
    success_rate = (stats.success_calls / stats.total_calls) if stats.total_calls else 0.0
    return UsageStatsResponse(
        total_calls=stats.total_calls,
        success_calls=stats.success_calls,
        success_rate=round(success_rate, 3),
        avg_duration_ms=stats.avg_duration_ms,
        last_used_at=stats.last_used_at,
        top_commands=list(stats.top_commands),
        calls_by_caller=dict(stats.calls_by_caller),
    )


@router.post("/{name}/spawn-external", response_model=SpawnExternalResponse)
async def spawn_external(
    name: str,
    request: Request,
    body: SpawnExternalRequest,
) -> SpawnExternalResponse:
    """Spawns an external Windows terminal (wt/pwsh) and runs either the
    install or the login command in it.

    Unlike POST /install (which runs its own asyncio subprocess pipeline
    + streams output to the UI), this endpoint is pure spawn-and-forget
    logic: we open a real terminal window (as the user expects when they
    type ``wt`` themselves) and let the user interact there. Status
    polling runs separately via /check.
    """
    from jarvis.clis.external_terminal import spawn_external_terminal

    reg = _require_registry(request)
    spec = reg.catalog().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")

    if body.kind == "install":
        if not body.method:
            return SpawnExternalResponse(
                ok=False,
                error="method required for kind=install",
            )
        installer = reg.installer()
        cmd_argv = installer.build_command(spec, body.method)  # type: ignore[arg-type]
        if not cmd_argv:
            return SpawnExternalResponse(
                ok=False,
                error=f"No install method '{body.method}' for {name}",
            )
        command = " ".join(cmd_argv)
        title = f"Install: {spec.display_name}"
    else:  # kind == "login"
        if spec.auth.type != "oauth_cli" or not spec.auth.login_command:
            return SpawnExternalResponse(
                ok=False,
                error=f"{name} has no interactive login_command (auth.type={spec.auth.type})",
            )
        command = " ".join(spec.auth.login_command)
        title = f"Login: {spec.display_name}"

    ok, method = spawn_external_terminal(command, cwd=None, title=title)
    return SpawnExternalResponse(
        ok=ok,
        method=method,
        command=command if ok else None,
        error=None if ok else "No external terminal available (wt/pwsh/powershell)",
    )


@router.post("/{name}/install", response_model=InstallStartResponse)
async def install_cli(
    name: str,
    request: Request,
    body: InstallRequest,
) -> InstallStartResponse:
    reg = _require_registry(request)
    spec = reg.catalog().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    installer = reg.installer()
    bus = reg.bus()

    def _on_line(job_id: str, line: str) -> None:
        if bus is None:
            return
        from jarvis.core.events import CliInstallProgress

        _publish_safe(
            bus,
            CliInstallProgress(
                source_layer="clis.installer",
                cli_name=name,
                job_id=job_id,
                line=line,
                done=False,
            ),
        )

    def _on_done(job) -> None:  # type: ignore[no-untyped-def]
        if bus is None:
            return
        from jarvis.core.events import CliInstallProgress

        _publish_safe(
            bus,
            CliInstallProgress(
                source_layer="clis.installer",
                cli_name=name,
                job_id=job.job_id,
                line="",
                done=True,
                exit_code=job.result.exit_code if job.result else None,
            ),
        )
        asyncio.create_task(reg.refresh_status(name), name=f"cli-refresh-after-install-{name}")

    job = installer.start(
        spec,
        body.method,  # type: ignore[arg-type]
        on_line=_on_line,
        on_done=_on_done,
    )
    if job is None:
        return InstallStartResponse(
            ok=False,
            job_id="",
            command="",
            error=f"Method '{body.method}' not available for {name}",
        )
    return InstallStartResponse(
        ok=True,
        job_id=job.job_id,
        command=" ".join(job.command),
    )


@router.delete("/{name}/install/{job_id}")
async def cancel_install(name: str, job_id: str, request: Request) -> dict[str, bool]:
    reg = _require_registry(request)
    ok = reg.installer().cancel(job_id)
    return {"ok": ok}


@router.post("/{name}/connect", response_model=ConnectResponse)
async def connect_cli(
    name: str,
    request: Request,
    body: ConnectRequest,
) -> ConnectResponse:
    reg = _require_registry(request)
    spec = reg.catalog().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    auth = reg.auth()
    bus = reg.bus()

    if body.mode == "api_key":
        if spec.auth.type != "api_key":
            return ConnectResponse(
                ok=False,
                status="error",
                error=f"{name} uses auth.type='{spec.auth.type}', not 'api_key'",
            )
        ok, err = await auth.connect_api_key(
            spec,
            body.secrets or {},
            validate=body.validate_creds,
        )
        status = await reg.refresh_status(name)
        return ConnectResponse(
            ok=ok,
            status=_status_string(spec, status) if status else "error",
            error=err,
        )

    if spec.auth.type != "oauth_cli":
        return ConnectResponse(
            ok=False,
            status="error",
            error=f"{name} uses auth.type='{spec.auth.type}', not 'oauth_cli'",
        )
    from uuid import uuid4

    job_id = str(uuid4())

    def _on_line(line: str) -> None:
        if bus is None:
            return
        from jarvis.core.events import CliConnectProgress

        _publish_safe(
            bus,
            CliConnectProgress(
                source_layer="clis.auth",
                cli_name=name,
                job_id=job_id,
                line=line,
                step="polling",
                done=False,
            ),
        )

    handle = auth.start_oauth_login(spec, job_id=job_id, on_line=_on_line)
    if handle is None:
        return ConnectResponse(
            ok=False, status="error", error="login already active or not available"
        )

    if bus is not None:
        from jarvis.core.events import CliConnectProgress

        _publish_safe(
            bus,
            CliConnectProgress(
                source_layer="clis.auth",
                cli_name=name,
                job_id=job_id,
                line="Login started",
                step="browser_open",
                done=False,
            ),
        )

    async def _await_and_finalize() -> None:
        result = await handle.task
        if bus is not None:
            from jarvis.core.events import CliConnectProgress

            _publish_safe(
                bus,
                CliConnectProgress(
                    source_layer="clis.auth",
                    cli_name=name,
                    job_id=job_id,
                    line=f"flow {result}",
                    step=result,
                    done=True,
                ),
            )
        await reg.refresh_status(name)

    asyncio.create_task(_await_and_finalize(), name=f"cli-connect-finalize-{name}")

    return ConnectResponse(ok=True, status="checking", job_id=job_id, error=None)


@router.post("/{name}/disconnect", response_model=DisconnectResponse)
async def disconnect_cli(name: str, request: Request) -> DisconnectResponse:
    reg = _require_registry(request)
    spec = reg.catalog().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    auth = reg.auth()
    if spec.auth.type == "api_key":
        ok = auth.disconnect_api_key(spec)
        await reg.refresh_status(name)
        return DisconnectResponse(ok=ok, error=None if ok else "keyring delete failed")
    if spec.auth.type == "oauth_cli":
        ok, err = await auth.disconnect_oauth(spec)
        await reg.refresh_status(name)
        return DisconnectResponse(ok=ok, error=err)
    return DisconnectResponse(
        ok=False,
        error=f"auth.type '{spec.auth.type}' does not support disconnect",
    )


@router.post("/custom", response_model=CliDetail)
async def register_custom(payload: CustomCliPayload, request: Request) -> CliDetail:
    reg = _require_registry(request)
    if reg.catalog().get(payload.name) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"'{payload.name}' existiert bereits (Seed oder Custom)",
        )
    from jarvis.clis.spec import (
        AuthConfig,
        CliSpec,
        InstallMethods,
        RiskConfig,
    )

    spec = CliSpec(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        homepage=payload.homepage,
        binary_name=payload.binary_name,
        check_command=tuple(payload.check_command),
        version_parse_regex=payload.version_parse_regex,
        install=InstallMethods(manual_url=payload.install_manual_url or payload.homepage),
        auth=AuthConfig(
            type=payload.auth_mode,
            login_command=tuple(payload.login_command) if payload.login_command else None,
            logout_command=tuple(payload.logout_command) if payload.logout_command else None,
            status_command=tuple(payload.status_command),
            status_parse=payload.status_parse,  # type: ignore[arg-type]
            secret_keys=tuple(payload.secret_keys),
            env_vars=tuple(payload.env_vars),
        ),
        risk=RiskConfig(
            default_tier=payload.risk_tier,
            blacklist_patterns=tuple(payload.deny_patterns),
            whitelist_patterns=tuple(payload.allow_patterns),
        ),
        tool_schema_examples=(),
        icon=payload.icon,
        category=payload.category,
        source="custom",
    )
    reg.catalog().register_custom(spec)
    await reg.refresh_status(payload.name)
    return await get_cli(payload.name, request)


@router.delete("/custom/{name}")
async def unregister_custom(name: str, request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    ok = reg.catalog().remove_custom(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Custom CLI '{name}' does not exist")
    return {"ok": True}


@router.delete("/{name}/usage")
async def clear_usage(name: str, request: Request) -> dict[str, int]:
    reg = _require_registry(request)
    if reg.catalog().get(name) is None:
        raise HTTPException(status_code=404, detail=f"CLI '{name}' not in catalog")
    deleted = reg.usage_log().delete_for(name)
    return {"deleted": deleted}


# ----------------------------------------------------------------------
# Test Hub — drive a natural-language instruction through the real brain
# ----------------------------------------------------------------------


class _CliCallCapture:
    """Captures ``cli_<name>`` tool calls made during one brain turn.

    Two capture channels combined:

    1. **Bus events** (``ActionProposed`` / ``ActionExecuted``): give the tool
       name, the exact command, the resolved risk tier, success and duration —
       without touching the brain's internals.
    2. **Tool wrappers**: each ``cli_<name>`` tool on the brain is temporarily
       wrapped so we also capture the structured ``ToolResult`` (exit code,
       stdout, stderr) — these never reach the bus (privacy: the usage log does
       not persist stdout). The wrapper is restored after the turn.

    The endpoint is OFF the voice critical path, so the modest wrapping cost is
    acceptable. Never call ``Tool.execute()`` directly here (AP-3); execution
    still flows through the brain's ``ToolExecutor``.
    """

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._by_trace: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _is_cli_tool_name(name: str) -> bool:
        return name.startswith("cli_")

    async def on_proposed(self, ev: Any) -> None:
        if not self._is_cli_tool_name(ev.tool_name):
            return
        args = ev.args if isinstance(ev.args, dict) else {}
        step = {
            "tool": ev.tool_name,
            "command": str(args.get("command", "")),
            "risk_tier": str(ev.risk_tier),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "success": False,
        }
        self.steps.append(step)
        self._by_trace[str(ev.trace_id)] = step

    async def on_executed(self, ev: Any) -> None:
        if not self._is_cli_tool_name(ev.tool_name):
            return
        step = self._by_trace.get(str(ev.trace_id))
        if step is None:
            return
        step["success"] = bool(ev.success)
        if not step["duration_ms"]:
            step["duration_ms"] = int(ev.duration_ms or 0)
        if ev.error and not step["stderr"]:
            step["stderr"] = str(ev.error)

    def wrap_tools(self, tools: dict[str, Any]) -> dict[str, Any]:
        """Wrap each ``cli_<name>`` tool's ``execute`` to capture the result.

        Returns a mapping of ``name -> original_execute`` so the caller can
        restore the originals in a ``finally`` block.
        """
        originals: dict[str, Any] = {}
        for name, tool in tools.items():
            if not self._is_cli_tool_name(name):
                continue
            original = tool.execute
            originals[name] = original

            def _make_wrapper(orig: Any, tool_name: str) -> Any:
                async def _wrapped(args: Any, ctx: Any) -> Any:
                    result = await orig(args, ctx)
                    self._record_result(tool_name, args, result)
                    return result

                return _wrapped

            tool.execute = _make_wrapper(original, name)  # type: ignore[method-assign]
        return originals

    @staticmethod
    def restore_tools(tools: dict[str, Any], originals: dict[str, Any]) -> None:
        for name, original in originals.items():
            tool = tools.get(name)
            if tool is not None:
                tool.execute = original  # type: ignore[method-assign]

    def _record_result(self, tool_name: str, args: Any, result: Any) -> None:
        command = ""
        if isinstance(args, dict):
            command = str(args.get("command", ""))
        # Find the matching pending step (last one for this tool without output)
        step = None
        for s in reversed(self.steps):
            if s["tool"] == tool_name and s["exit_code"] is None and not s["stdout"]:
                step = s
                break
        if step is None:
            step = {
                "tool": tool_name,
                "command": command,
                "risk_tier": None,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "duration_ms": 0,
                "success": False,
            }
            self.steps.append(step)
        if command and not step["command"]:
            step["command"] = command
        output = getattr(result, "output", None)
        if isinstance(output, dict):
            step["exit_code"] = output.get("exit_code")
            step["stdout"] = str(output.get("stdout", ""))
            step["stderr"] = str(output.get("stderr", ""))
            step["duration_ms"] = int(output.get("duration_ms", 0) or 0)
        step["success"] = bool(getattr(result, "success", False))
        if getattr(result, "error", None) and not step["stderr"]:
            step["stderr"] = str(result.error)


def _resolve_test_run_brain(request: Request) -> Any:
    """Reuse the app's running brain when usable, else build a default one.

    The brain must resolve ``cfg.brain.primary`` with its normal fallback chain
    — never a hardcoded Claude/Anthropic client (the user has no Anthropic API
    account, AP-6). ``build_default_brain`` already does this.
    """
    brain = getattr(request.app.state, "brain", None)
    if brain is not None and hasattr(brain, "generate") and getattr(brain, "_tools", None):
        return brain
    from jarvis.brain.factory import build_default_brain

    return build_default_brain()


@router.post("/test-run", response_model=TestRunResponse)
async def test_run(body: TestRunRequest, request: Request) -> TestRunResponse:
    """Run a natural-language instruction through the real brain and report
    which ``cli_<name>`` tool ran, the exact command, the risk tier, exit code,
    output, duration, and the brain's spoken-style summary.

    Execution flows through the brain's normal ``ToolExecutor`` (risk-tier /
    whitelist / plausibility) — never ``Tool.execute()`` directly (AP-3).
    """
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction must not be empty")

    try:
        brain = _resolve_test_run_brain(request)
    except Exception as exc:  # noqa: BLE001
        log.exception("test-run: brain resolution failed")
        return TestRunResponse(
            ok=False,
            instruction=instruction,
            error=f"Brain not available: {exc}",
        )

    bus = getattr(brain, "_bus", None)
    tools = getattr(brain, "_tools", {}) or {}

    capture = _CliCallCapture()
    from jarvis.core.events import ActionExecuted, ActionProposed

    if bus is not None:
        bus.subscribe(ActionProposed, capture.on_proposed)
        bus.subscribe(ActionExecuted, capture.on_executed)
    originals = capture.wrap_tools(tools)

    # Build the instruction text. The hint is a soft nudge — the brain still
    # chooses the tool. We keep it explicit so the test-hub is deterministic.
    prompt = instruction
    if body.cli_hint:
        prompt = f"{instruction}\n\n(Use the {body.cli_hint} CLI tool for this.)"

    summary = ""
    error: str | None = None
    ok = False
    try:
        summary = await asyncio.wait_for(
            brain.generate(prompt, use_history=False),
            timeout=_TEST_RUN_TIMEOUT_S,
        )
        ok = True
    except TimeoutError:
        error = f"Brain turn timed out after {_TEST_RUN_TIMEOUT_S:.0f}s"
    except Exception as exc:  # noqa: BLE001
        log.exception("test-run: brain turn failed")
        error = str(exc)
    finally:
        capture.restore_tools(tools, originals)
        if bus is not None:
            try:
                bus.unsubscribe(ActionProposed, capture.on_proposed)
                bus.unsubscribe(ActionExecuted, capture.on_executed)
            except Exception as exc:  # noqa: BLE001
                log.debug("test-run: bus unsubscribe failed: %s", exc)

    steps = [
        TestRunStep(
            tool=s["tool"],
            command=s["command"],
            exit_code=s["exit_code"],
        )
        for s in capture.steps
    ]
    last = capture.steps[-1] if capture.steps else None

    if last is None:
        # The brain answered without calling any CLI tool. Not an error per se
        # (it may have answered from memory), but the test-hub flags it so the
        # UI can show "no CLI tool was called".
        return TestRunResponse(
            ok=ok and error is None,
            instruction=instruction,
            tool_called=None,
            summary=summary,
            error=error or "No cli_<name> tool was called for this instruction.",
            steps=steps,
        )

    tool_ok = last["success"] and (last["exit_code"] in (0, None))
    return TestRunResponse(
        ok=bool(ok and error is None and tool_ok),
        instruction=instruction,
        tool_called=last["tool"],
        command=last["command"],
        risk_tier=last["risk_tier"],
        exit_code=last["exit_code"],
        stdout=last["stdout"],
        stderr=last["stderr"],
        duration_ms=last["duration_ms"],
        summary=summary,
        error=error,
        steps=steps,
    )


__all__ = ["router"]
