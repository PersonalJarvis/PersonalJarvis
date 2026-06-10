"""Shared fakes for CLI capability-provider / prompt-section tests."""
from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.clis.spec import (
    AuthConfig,
    CliCapabilityDecl,
    CliSpec,
    InstallMethods,
    RiskConfig,
)


def make_spec(name: str, domains: tuple[str, ...] = ("repos",)) -> CliSpec:
    return CliSpec(
        name=name,
        display_name=name.upper(),
        description=f"{name} CLI.",
        homepage="https://example.com",
        binary_name=name,
        check_command=(name, "--version"),
        version_parse_regex=r"(\S+)",
        install=InstallMethods(manual_url="https://example.com"),
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        tool_schema_examples=(f"{name} list", f"{name} status"),
        capabilities=(
            CliCapabilityDecl(
                domains=domains,
                verbs=("zeig", "list", "show"),
                objects=("pull request", "issue"),
                description=f"{name} test capability.",
            ),
        ),
    )


@dataclass
class FakeTool:
    name: str


class _FakeCatalog:
    def __init__(self, specs: dict) -> None:
        self._specs = specs

    def all(self) -> dict:
        return self._specs


@dataclass
class FakeCliRegistry:
    specs: dict
    active: list
    status: dict = field(default_factory=dict)

    def catalog(self) -> _FakeCatalog:
        return _FakeCatalog(self.specs)

    def active_tools(self) -> list:
        return self.active

    def all_status(self) -> dict:
        return self.status
