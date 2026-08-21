"""Tests for the in-app feedback REST endpoint (finding 13, AP-23 wave 2).

Contract (see jarvis/ui/web/feedback_routes.py):
- POST /api/feedback -> {"ok": bool, "status": str, "detail": str, "github_url": str|None}

When no Discord webhook is configured (the common case for every downloader —
``discord_feedback_webhook_url`` is a maintainer-only operator credential that
was never shipped), the endpoint must degrade HONESTLY: point the end user at
the project's public GitHub issues page instead of instructing them to
configure a credential that is meaningless for them.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

GITHUB_ISSUES_URL = "https://github.com/PersonalJarvis/PersonalJarvis/issues"


def _client() -> TestClient:
    from jarvis.ui.web.feedback_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


def _payload(**overrides: object) -> dict:
    body = {
        "type": "bug",
        "title": "Something broke",
        "description": "It broke when I clicked the button.",
    }
    body.update(overrides)
    return body


def test_no_webhook_configured_points_to_github_issues(client: TestClient, monkeypatch) -> None:
    """No webhook configured -> honest downloader-facing fallback: a GitHub
    issues URL, not an instruction to set an operator-only credential."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.post("/api/feedback", json=_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    # The response must carry a URL the frontend can render as "report it on
    # GitHub" rather than dead-ending the user.
    assert body.get("github_url") == GITHUB_ISSUES_URL
    assert GITHUB_ISSUES_URL in body["detail"]


def test_no_webhook_configured_does_not_instruct_setting_a_credential(
    client: TestClient, monkeypatch
) -> None:
    """The old behavior told the END USER to set a Discord webhook credential
    ('discord_feedback_webhook_url') — meaningless for a downloader who is not
    the project operator. That misdirection must be gone."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.post("/api/feedback", json=_payload())

    detail_lower = resp.json()["detail"].lower()
    assert "discord_feedback_webhook_url" not in detail_lower
    assert "environment variable" not in detail_lower
    assert "credential" not in detail_lower


# ----------------------------------------------------------------------
# GET /api/feedback/status — the capability probe the form renders from
# ----------------------------------------------------------------------


def test_status_not_configured_offers_github_fallback(client: TestClient, monkeypatch) -> None:
    """Fresh install (no webhook) -> configured=False plus everything the
    frontend needs to compose a prefilled GitHub issue instead."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.get("/api/feedback/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["github_url"] == GITHUB_ISSUES_URL
    # The same system fields the POST route would attach server-side, plus the
    # dropdown-ready OS the bug form needs.
    assert set(body["context"]) == {"app_version", "os", "python", "os_choice"}
    assert all(isinstance(v, str) and v for v in body["context"].values())


def test_status_configured_never_leaks_the_webhook_url(client: TestClient, monkeypatch) -> None:
    """Operator install (webhook present) -> configured=True; the webhook URL
    itself (an operator credential) must never appear in the response."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    webhook_url = "https://discord.com/api/webhooks/123/abc"  # test dummy
    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: webhook_url)

    resp = client.get("/api/feedback/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert webhook_url not in resp.text


def test_empty_screenshot_payload_sends_no_attachment(client: TestClient, monkeypatch) -> None:
    """A data-URL without a base64 payload decodes to b"" — the dispatch must
    fall back to the plain JSON webhook call instead of attaching an empty
    file to Discord."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    captured: dict = {}

    class _FakeResponse:
        is_success = True
        status_code = 204
        text = ""

    class _FakeAsyncClient:
        def __init__(self, timeout: float | None = None) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, **kwargs: object) -> _FakeResponse:
            captured.update({"url": url, **kwargs})
            return _FakeResponse()

    monkeypatch.setattr(
        feedback_routes, "get_secret",
        lambda *a, **k: "https://discord.com/api/webhooks/123/abc",
    )
    monkeypatch.setattr(feedback_routes.httpx, "AsyncClient", _FakeAsyncClient)

    resp = client.post(
        "/api/feedback", json=_payload(screenshot="data:image/png;base64,")
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    # Plain JSON dispatch, no multipart upload of a zero-byte "image".
    assert "json" in captured
    assert "files" not in captured


def test_app_version_pyproject_fallback_reads_repo_root() -> None:
    """The pyproject.toml fallback must resolve to the real repo root — it
    regressed once by pointing one directory ABOVE it (parents[4])."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    root = Path(feedback_routes.__file__).resolve().parents[3]
    assert (root / "pyproject.toml").is_file()


# ----------------------------------------------------------------------
# Issue-form wiring
#
# The section's primary path is a PREFILLED GitHub issue on one of the repo's
# issue forms. That is what applies the `bug` / `enhancement` label and the
# title prefix; an issue opened without `?template=` lands blank, unlabelled,
# and gets sorted by hand. The wiring is therefore a contract between three
# files that nothing else checks: feedback_routes.py, the two YAML forms, and
# the frontend that composes the URL from `templates` + `context`.
# ----------------------------------------------------------------------


def _template_dir() -> Path:
    import jarvis.ui.web.feedback_routes as feedback_routes

    root = Path(feedback_routes.__file__).resolve().parents[3]
    return root / ".github" / "ISSUE_TEMPLATE"


def test_status_names_the_issue_forms(client: TestClient) -> None:
    """Every report type maps to a form (or to None, meaning 'not the tracker')."""
    resp = client.get("/api/feedback/status")

    assert resp.status_code == 200
    assert resp.json()["templates"] == {
        "bug": "bug_report.yml",
        "idea": "feature_request.yml",
        "question": None,
    }


def test_named_issue_forms_exist_in_the_repo() -> None:
    """A renamed or deleted form would silently dead-end the primary path:
    GitHub answers `?template=<missing>` with the plain blank-issue editor and
    every prefilled field is dropped."""
    from jarvis.ui.web.feedback_routes import ISSUE_TEMPLATES

    for report_type, filename in ISSUE_TEMPLATES.items():
        if filename is None:
            continue
        assert (_template_dir() / filename).is_file(), (
            f"{report_type} points at a missing issue form: {filename}"
        )


def test_prefilled_field_ids_exist_in_their_forms() -> None:
    """The frontend prefills by FIELD ID. GitHub ignores an unknown id without
    an error, so a renamed field loses that part of the report silently."""
    yaml = pytest.importorskip("yaml")

    expected: dict[str, set[str]] = {
        # Mirrors the field ids FeedbackView.tsx composes into the issue URL.
        "bug_report.yml": {"what-happened", "steps", "os", "python"},
        "feature_request.yml": {"problem", "solution"},
    }
    for filename, field_ids in expected.items():
        form = yaml.safe_load((_template_dir() / filename).read_text(encoding="utf-8"))
        present = {b["id"] for b in form["body"] if "id" in b}
        missing = field_ids - present
        assert not missing, f"{filename} lost prefilled field(s): {sorted(missing)}"


def test_os_choice_matches_a_dropdown_option_of_the_bug_form() -> None:
    """GitHub drops a dropdown prefill that is not an EXACT option string, so
    the OS silently vanishes from the report if either side drifts."""
    yaml = pytest.importorskip("yaml")
    from jarvis.ui.web.feedback_routes import _os_choice

    form = yaml.safe_load((_template_dir() / "bug_report.yml").read_text(encoding="utf-8"))
    options = next(b["attributes"]["options"] for b in form["body"] if b.get("id") == "os")

    assert _os_choice() in options


def test_os_choice_covers_every_platform(monkeypatch) -> None:
    """Each supported platform maps onto an option — including the headless
    Linux box, which is the distinction triage asks about first."""
    yaml = pytest.importorskip("yaml")
    import jarvis.ui.web.feedback_routes as feedback_routes

    form = yaml.safe_load((_template_dir() / "bug_report.yml").read_text(encoding="utf-8"))
    options = next(b["attributes"]["options"] for b in form["body"] if b.get("id") == "os")

    for system, expected in [("Windows", "Windows"), ("Darwin", "macOS")]:
        monkeypatch.setattr(feedback_routes.platform, "system", lambda s=system: s)
        assert feedback_routes._os_choice() == expected
        assert expected in options

    monkeypatch.setattr(feedback_routes.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert feedback_routes._os_choice() == "Linux"

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    headless = feedback_routes._os_choice()
    assert headless == "Headless server / VPS"
    assert headless in options


# ----------------------------------------------------------------------
# GET /api/feedback/board — the public read of open issues
#
# No token and no GitHub login: someone without an account still sees their
# idea is already tracked, which is what prevents the duplicate they would
# otherwise file. The 60/hour unauthenticated IP budget makes the cache and
# the stale-on-failure behaviour part of the contract, not an optimisation.
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_board_cache():
    """The board cache is process-wide; leaking it across tests would let one
    test's fixture answer another's request."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    feedback_routes._board_cache = None
    feedback_routes._board_cache_at = 0.0
    yield
    feedback_routes._board_cache = None
    feedback_routes._board_cache_at = 0.0


def _issue(number: int, title: str, *, upvotes: int = 0, comments: int = 0) -> dict:
    return {
        "number": number,
        "title": title,
        "html_url": f"{GITHUB_ISSUES_URL}/{number}",
        "reactions": {"+1": upvotes},
        "comments": comments,
    }


def test_board_drops_pull_requests() -> None:
    """/issues returns pull requests too, and a PR is not something a user
    asked for."""
    from jarvis.ui.web.feedback_routes import _entries_from_issues

    raw = [_issue(1, "A real request"), {**_issue(2, "A PR"), "pull_request": {"url": "x"}}]

    entries = _entries_from_issues(raw)

    assert [e.number for e in entries] == [1]


def test_board_ranks_by_upvotes() -> None:
    """The 👍 count is the closest thing the tracker has to a vote, and the
    only reason the list is worth showing."""
    from jarvis.ui.web.feedback_routes import _entries_from_issues

    raw = [_issue(1, "meh", upvotes=1), _issue(2, "popular", upvotes=9), _issue(3, "new")]

    entries = _entries_from_issues(raw)

    assert [e.number for e in entries] == [2, 1, 3]
    assert entries[0].upvotes == 9


def test_board_survives_a_malformed_row() -> None:
    """A row missing the fields the board renders is skipped, not fatal — one
    odd item must never blank the whole list."""
    from jarvis.ui.web.feedback_routes import _entries_from_issues

    raw = [{"number": "not-an-int", "title": "broken"}, {}, _issue(7, "fine")]

    assert [e.number for e in _entries_from_issues(raw)] == [7]


def test_board_caches_between_requests(client: TestClient, monkeypatch) -> None:
    """Several desktop windows share one backend; without the cache each open
    window would spend its own pair of requests from the same IP budget."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    calls: list[str] = []

    async def _fake_fetch(_client: object, label: str) -> list:
        calls.append(label)
        return [feedback_routes.BoardEntry(
            number=1, title=label, url=GITHUB_ISSUES_URL, upvotes=0, comments=0
        )]

    monkeypatch.setattr(feedback_routes, "_fetch_label", _fake_fetch)

    first = client.get("/api/feedback/board").json()
    second = client.get("/api/feedback/board").json()

    assert first["available"] is True
    assert first == second
    # One refresh only: two labels, fetched once.
    assert calls == ["enhancement", "bug"]


def test_board_serves_the_last_good_lists_after_a_failed_refresh(
    client: TestClient, monkeypatch
) -> None:
    """A rate limit or a network blip is no reason to tell the user nobody
    ever asked for anything."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    feedback_routes._board_cache = feedback_routes.FeedbackBoard(
        available=True,
        ideas=[feedback_routes.BoardEntry(
            number=5, title="Dark mode", url=GITHUB_ISSUES_URL, upvotes=3, comments=1
        )],
        bugs=[],
    )
    # Expired, so the route attempts a refresh — which fails.
    feedback_routes._board_cache_at = 0.0

    async def _boom(_client: object, _label: str) -> list:
        raise feedback_routes.httpx.ConnectError("no network")

    monkeypatch.setattr(feedback_routes, "_fetch_label", _boom)

    body = client.get("/api/feedback/board").json()

    assert body["available"] is True
    assert [e["title"] for e in body["ideas"]] == ["Dark mode"]


def test_board_unavailable_when_it_never_loaded(client: TestClient, monkeypatch) -> None:
    """With no cached copy the board reports unavailable WITH a reason, so the
    frontend hides it instead of rendering an empty list that reads as
    'nobody ever asked for anything'."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    async def _boom(_client: object, _label: str) -> list:
        raise feedback_routes.httpx.ConnectError("no network")

    monkeypatch.setattr(feedback_routes, "_fetch_label", _boom)

    body = client.get("/api/feedback/board").json()

    assert body["available"] is False
    assert body["detail"] == "unreachable"
    assert body["ideas"] == [] and body["bugs"] == []


def test_board_failure_does_not_freeze_out_the_next_retry(
    client: TestClient, monkeypatch
) -> None:
    """A failed refresh must not advance the cache timestamp — otherwise one
    blip would serve stale lists for a full TTL."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    state = {"fail": True}

    async def _flaky(_client: object, label: str) -> list:
        if state["fail"]:
            raise feedback_routes.httpx.ConnectError("no network")
        return [feedback_routes.BoardEntry(
            number=2, title=label, url=GITHUB_ISSUES_URL, upvotes=1, comments=0
        )]

    monkeypatch.setattr(feedback_routes, "_fetch_label", _flaky)

    assert client.get("/api/feedback/board").json()["available"] is False
    state["fail"] = False
    assert client.get("/api/feedback/board").json()["available"] is True
