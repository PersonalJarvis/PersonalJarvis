"""``POST /api/marketplace/plugins/upload`` — dropping your own plugin in.

A plugin here is a *manifest*, not a code package: `plugin.json` plus an
optional `mcp.json` beside it. That makes the upload flow shorter than the
skill one but adds a question the skill flow never faces — who published this?

On the community path the answer comes from the registry index, precisely so a
manifest cannot claim someone else's identity. A local upload has no such
witness. These tests pin that the route does not invent one: no publisher, no
version, no source URL, and a `source` that says "local" rather than borrowing
the community badge and the review it implies.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from jarvis.marketplace.agent_plugins_loader import EXTENSION_NAMESPACE
from jarvis.ui.web import marketplace_routes as routes


def _plugin_json(name: str = "todo-fox") -> dict:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "description": "Tasks and reminders from TodoFox",
        "version": "1.2.0",
        "license": "MIT",
        "extensions": {
            EXTENSION_NAMESPACE: {
                "display_name": "TodoFox",
                "category": "Lists & Tasks",
                "logo_slug": "todofox",
                "auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://todofox.example/settings/tokens",
                    "token_prefix": "tfx_",
                    "validation_endpoint": "https://api.todofox.example/v1/me",
                    "instruction_md": "Create a token in Settings.",
                },
                "mcp_auth_header_template": (
                    "Authorization: Bearer ${plugin_todo-fox_access_token}"
                ),
            }
        },
    }


def _mcp_json() -> dict:
    return {
        "mcpServers": {
            "todo-fox": {
                "type": "streamable-http",
                "url": "https://mcp.todofox.example/mcp",
            }
        }
    }


def _upload(path: str, data: bytes) -> UploadFile:
    return UploadFile(filename=Path(path).name, file=io.BytesIO(data))


def _parts(entries: dict[str, bytes]) -> tuple[list[UploadFile], str]:
    files = [_upload(path, data) for path, data in entries.items()]
    return files, json.dumps(list(entries))


def _blob(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    return buffer.getvalue()


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> list:
    """Captures what would be written, so nothing touches the real catalog."""
    saved: list = []
    monkeypatch.setattr(
        "jarvis.marketplace.community_install.install_plugin_spec",
        lambda spec: saved.append(spec),
    )
    monkeypatch.setattr(routes, "_refresh_plugin_in_live_registry", lambda plugin_id: None)
    return saved


# ----------------------------------------------------------------------
# Installing
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dropped_manifest_is_installed(installed: list) -> None:
    files, paths = _parts({"plugin.json": _blob(_plugin_json())})

    result = await routes.upload_plugin(files, paths)

    assert result["ok"] is True
    assert result["plugin"]["id"] == "todo-fox"
    assert result["plugin"]["status"] == "not_connected"
    assert [spec.id for spec in installed] == ["todo-fox"]


@pytest.mark.asyncio
async def test_the_upload_claims_no_publisher_it_cannot_prove(installed: list) -> None:
    """The whole reason the community path takes identity from the index."""
    files, paths = _parts({"plugin.json": _blob(_plugin_json())})

    await routes.upload_plugin(files, paths)

    spec = installed[0]
    assert spec.source == "local"
    assert spec.publisher is None
    assert spec.source_url is None


@pytest.mark.asyncio
async def test_an_mcp_json_beside_the_manifest_travels_with_it(installed: list) -> None:
    files, paths = _parts(
        {
            "todo-fox/plugin.json": _blob(_plugin_json()),
            "todo-fox/mcp.json": _blob(_mcp_json()),
        }
    )

    await routes.upload_plugin(files, paths)

    assert installed[0].mcp_server is not None


@pytest.mark.asyncio
async def test_an_mcp_json_from_another_folder_is_not_grafted_on(
    installed: list,
) -> None:
    """A stray server from elsewhere in the tree must not attach itself."""
    files, paths = _parts(
        {
            "plugins/todo-fox/plugin.json": _blob(_plugin_json()),
            "plugins/other-thing/mcp.json": _blob(_mcp_json()),
        }
    )

    await routes.upload_plugin(files, paths)

    assert installed[0].mcp_server is None


@pytest.mark.asyncio
async def test_a_manifest_buried_in_a_repository_zip_is_found(installed: list) -> None:
    archive = _zip_bytes(
        {
            "repo-main/README.md": b"# repo",
            "repo-main/plugins/todo-fox/plugin.json": _blob(_plugin_json()),
        }
    )

    result = await routes.upload_plugin([_upload("repo.zip", archive)], None)

    assert result["plugin"]["id"] == "todo-fox"


# ----------------------------------------------------------------------
# Refusing
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_upload_without_a_manifest_is_a_400(installed: list) -> None:
    files, paths = _parts({"readme.md": b"no manifest here"})

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin(files, paths)
    assert exc.value.status_code == 400
    assert "plugin.json" in exc.value.detail
    assert installed == []


@pytest.mark.asyncio
async def test_an_upload_holding_several_plugins_is_refused_by_name(
    installed: list,
) -> None:
    archive = _zip_bytes(
        {
            "catalog/one/plugin.json": _blob(_plugin_json("one-fox")),
            "catalog/two/plugin.json": _blob(_plugin_json("two-fox")),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin([_upload("catalog.zip", archive)], None)
    assert exc.value.status_code == 400
    assert "2 plugins" in exc.value.detail
    assert installed == []


@pytest.mark.asyncio
async def test_a_manifest_that_does_not_convert_is_a_400(installed: list) -> None:
    """No Jarvis auth block — the manifest is not one we can install."""
    broken = _plugin_json()
    broken["extensions"] = {}
    files, paths = _parts({"plugin.json": _blob(broken)})

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin(files, paths)
    assert exc.value.status_code == 400
    assert installed == []


@pytest.mark.asyncio
async def test_a_built_in_plugin_id_is_refused(
    installed: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "jarvis.marketplace.community_install.seed_plugin_ids",
        lambda: frozenset({"todo-fox"}),
    )
    files, paths = _parts({"plugin.json": _blob(_plugin_json())})

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin(files, paths)
    assert exc.value.status_code == 409
    assert "built-in" in exc.value.detail
    assert installed == []


@pytest.mark.asyncio
async def test_broken_json_is_refused_with_the_file_named(installed: list) -> None:
    files, paths = _parts({"plugin.json": b"{not json"})

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin(files, paths)
    assert exc.value.status_code == 400
    assert "plugin.json" in exc.value.detail


@pytest.mark.asyncio
async def test_a_zip_that_walks_out_of_the_folder_is_refused(
    installed: list, tmp_path: Path
) -> None:
    archive = _zip_bytes(
        {"plugin.json": _blob(_plugin_json()), "../../escaped.json": b"evil"}
    )

    with pytest.raises(HTTPException) as exc:
        await routes.upload_plugin([_upload("evil.zip", archive)], None)
    assert exc.value.status_code == 400
    assert installed == []


# ----------------------------------------------------------------------
# Inspecting
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inspect_reports_the_plugin_without_installing_it(
    installed: list,
) -> None:
    files, paths = _parts(
        {
            "todo-fox/plugin.json": _blob(_plugin_json()),
            "todo-fox/mcp.json": _blob(_mcp_json()),
            "todo-fox/.DS_Store": b"junk",
        }
    )

    report = await routes.inspect_plugin_upload(files, paths)

    assert report["ready"] is True
    assert report["problems"] == []
    assert report["plugin"]["id"] == "todo-fox"
    assert report["plugin"]["display_name"] == "TodoFox"
    assert report["plugin"]["auth_mode"] == "pat_paste"
    assert report["has_mcp"] is True
    assert set(report["files"]) == {"plugin.json", "mcp.json"}
    assert report["ignored"] == [".DS_Store"]
    assert installed == []


@pytest.mark.asyncio
async def test_inspect_reports_a_bad_manifest_as_a_problem(installed: list) -> None:
    """The dialog shows the reason instead of dying on an exception."""
    broken = _plugin_json()
    broken["extensions"] = {}
    files, paths = _parts({"plugin.json": _blob(broken)})

    report = await routes.inspect_plugin_upload(files, paths)

    assert report["ready"] is False
    assert report["plugin"] is None
    assert report["problems"]
