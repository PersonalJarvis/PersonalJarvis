"""Google Drive REST tool: reads the marketplace keyring token and calls the
Drive REST API v3 directly (native, MCP-free — the hosted Drive MCP 403s
consumer @gmail.com accounts, forensic 2026-07-23)."""

import json

import httpx
import pytest

from jarvis.plugins.tool.drive_rest import DriveRestTool


@pytest.mark.asyncio
async def test_list_files_builds_query_and_uses_bearer():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer at_123"
        captured["q"] = req.url.params.get("q")
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "f1",
                        "name": "budget.xlsx",
                        "mimeType": "application/vnd.ms-excel",
                        "modifiedTime": "2026-07-20T10:00:00Z",
                        "webViewLink": "https://drive.google.com/file/d/f1/view",
                        "owners": [{"displayName": "Ruben", "emailAddress": "r@x.com"}],
                    }
                ]
            },
        )

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.list_files(search_text="budget", max_results=5)
    # The plain search_text expands to a name/fullText clause AND excludes trash.
    assert "name contains 'budget'" in captured["q"]
    assert "fullText contains 'budget'" in captured["q"]
    assert "trashed = false" in captured["q"]
    # Slim projection: webViewLink -> url, single owner display name.
    assert out["files"][0] == {
        "id": "f1",
        "name": "budget.xlsx",
        "mimeType": "application/vnd.ms-excel",
        "modifiedTime": "2026-07-20T10:00:00Z",
        "size": None,
        "url": "https://drive.google.com/file/d/f1/view",
        "owner": "Ruben",
    }
    assert out["count"] == 1


@pytest.mark.asyncio
async def test_list_files_empty_search_lists_recent_untrashed():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["q"] = req.url.params.get("q")
        captured["orderBy"] = req.url.params.get("orderBy")
        return httpx.Response(200, json={"files": []})

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    await tool.list_files()
    assert captured["q"] == "trashed = false"
    assert captured["orderBy"] == "modifiedTime desc"


@pytest.mark.asyncio
async def test_read_file_exports_google_doc_as_text():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        url = str(req.url)
        if "/export" in url:
            assert req.url.params.get("mimeType") == "text/plain"
            return httpx.Response(200, text="This is the document body.")
        # metadata call
        return httpx.Response(
            200,
            json={
                "id": "d1",
                "name": "Notes",
                "mimeType": "application/vnd.google-apps.document",
                "webViewLink": "https://docs.google.com/d1",
            },
        )

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.read_file(file_id="d1")
    assert out["content"] == "This is the document body."
    assert out["name"] == "Notes"
    assert calls["n"] == 2  # metadata + export


@pytest.mark.asyncio
async def test_read_file_binary_is_not_downloaded_as_text():
    def handler(req: httpx.Request) -> httpx.Response:
        # Only the metadata call should ever fire for a binary file.
        assert "alt=media" not in str(req.url)
        return httpx.Response(
            200,
            json={"id": "b1", "name": "photo.png", "mimeType": "image/png"},
        )

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.read_file(file_id="b1")
    assert out["content"] is None
    assert "binary" in out["note"]


@pytest.mark.asyncio
async def test_create_file_sends_multipart_related():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["ctype"] = req.headers["content-type"]
        captured["body"] = req.content.decode("utf-8")
        captured["uploadType"] = req.url.params.get("uploadType")
        return httpx.Response(
            200,
            json={
                "id": "n1",
                "name": "todo.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/n1",
            },
        )

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.create_file(name="todo.txt", content="buy milk")
    assert captured["uploadType"] == "multipart"
    assert captured["ctype"].startswith("multipart/related; boundary=")
    assert '"name": "todo.txt"' in captured["body"]
    assert "buy milk" in captured["body"]
    assert out["url"] == "https://drive.google.com/n1"


@pytest.mark.asyncio
async def test_share_file_needs_email_or_public():
    tool = DriveRestTool(access_token_provider=lambda: "at_123")
    out = await tool.share_file(file_id="f1")
    assert "error" in out
    assert "email_address or public" in out["error"]


@pytest.mark.asyncio
async def test_share_file_with_email_posts_user_permission():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if "/permissions" in str(req.url):
            captured["perm"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "p1"})
        return httpx.Response(200, json={"id": "f1", "name": "f", "webViewLink": "u"})

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.share_file(file_id="f1", email_address="a@b.com", role="writer")
    assert captured["perm"] == {"type": "user", "role": "writer", "emailAddress": "a@b.com"}
    assert out["shared"] is True


@pytest.mark.asyncio
async def test_delete_file_issues_delete():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert str(req.url).endswith("/files/f1")
        return httpx.Response(204)

    tool = DriveRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.delete_file(file_id="f1")
    assert out == {"deleted": True, "file_id": "f1"}


@pytest.mark.asyncio
async def test_execute_returns_error_when_not_connected():
    tool = DriveRestTool(access_token_provider=lambda: None)
    result = await tool.execute({"action": "list_files"}, ctx=None)
    assert result.success is False
    assert "connect" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_list_files_refreshes_on_401_then_retries():
    calls = {"http": 0, "refresh": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        if calls["http"] == 1:
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"files": [{"id": "m9", "name": "x"}]})

    async def refresher() -> bool:
        calls["refresh"] += 1
        return True

    tool = DriveRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.list_files()
    assert out["files"][0]["id"] == "m9"
    assert calls["refresh"] == 1
    assert calls["http"] == 2


def test_risk_tiers_per_action():
    tool = DriveRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "list_files"}) == "safe"
    assert tool.risk_tier_for_args({"action": "read_file"}) == "safe"
    assert tool.risk_tier_for_args({"action": "create_file"}) == "monitor"
    assert tool.risk_tier_for_args({"action": "create_folder"}) == "monitor"
    assert tool.risk_tier_for_args({"action": "share_file"}) == "ask"
    assert tool.risk_tier_for_args({"action": "delete_file"}) == "ask"
    assert tool.risk_tier_for_args({"action": "purge_everything"}) == "ask"


def test_tool_contract_shape():
    tool = DriveRestTool()
    assert tool.name == "google_drive"
    assert "schema" in dir(tool)
    assert "list_files" in tool.schema["properties"]["action"]["enum"]
