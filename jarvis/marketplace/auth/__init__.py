"""Auth handlers for the redirect-based connect flow."""

from jarvis.marketplace.auth.base import (
    ERROR_DENIED,
    ERROR_MISCONFIGURED,
    ERROR_PORT_IN_USE,
    ERROR_PROVIDER_UNREACHABLE,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    AuthHandler,
    AuthSession,
    FlowRegistry,
    FlowResult,
    SessionKind,
    get_registry,
    now_ms,
    pkce_pair,
    random_state,
    sanitize_provider_error,
    session_id,
)
from jarvis.marketplace.auth.oauth_dcr import DcrConfig, HostedMcpDcrHandler
from jarvis.marketplace.auth.oauth_device import (
    DeviceFlowConfig,
    DeviceFlowHandler,
)
from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
)

__all__ = [
    "AuthHandler",
    "AuthSession",
    "DcrConfig",
    "DeviceFlowConfig",
    "DeviceFlowHandler",
    "ERROR_DENIED",
    "ERROR_MISCONFIGURED",
    "ERROR_PORT_IN_USE",
    "ERROR_PROVIDER_UNREACHABLE",
    "ERROR_TIMEOUT",
    "ERROR_UNKNOWN",
    "FlowRegistry",
    "FlowResult",
    "HostedMcpDcrHandler",
    "PkceLoopbackConfig",
    "PkceLoopbackHandler",
    "SessionKind",
    "get_registry",
    "now_ms",
    "pkce_pair",
    "random_state",
    "sanitize_provider_error",
    "session_id",
]
