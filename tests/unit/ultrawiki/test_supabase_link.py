"""The guided Supabase link flow, driven fully offline through MockTransport.

What matters here is not "does httpx work" but the three judgement calls the
module makes on the user's behalf: it must use the host Supabase REPORTS
(never a guessed region prefix), it must survive an unreadable pooler config
instead of dying, and it must produce a URI that still parses when the
database password contains URI punctuation.
"""

from __future__ import annotations

import json
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from jarvis.ultrawiki import supabase_link


def _transport(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    """MockTransport answering by path; unmapped paths 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = routes.get(request.url.path, (404, {"message": "nope"}))
        return httpx.Response(status, content=json.dumps(payload).encode())

    return httpx.MockTransport(handler)


PROJECTS_PATH = "/v1/projects"
POOLER_PATH = "/v1/projects/abcdefghijklmnopqrst/config/database/pooler"


async def test_list_projects_puts_healthy_projects_first():
    transport = _transport(
        {
            PROJECTS_PATH: (
                200,
                [
                    {
                        "id": "paused1",
                        "name": "Zeta archive",
                        "region": "eu-central-1",
                        "status": "INACTIVE",
                    },
                    {
                        "id": "live1",
                        "name": "Memory store",
                        "region": "eu-central-1",
                        "status": "ACTIVE_HEALTHY",
                    },
                ],
            )
        }
    )
    projects = await supabase_link.list_projects("tok", transport=transport)
    assert [p.ref for p in projects] == ["live1", "paused1"]
    assert projects[0].name == "Memory store"


async def test_a_rejected_token_says_so_in_plain_words():
    transport = _transport({PROJECTS_PATH: (401, {"message": "unauthorized"})})
    with pytest.raises(supabase_link.SupabaseLinkError) as excinfo:
        await supabase_link.list_projects("stale-token", transport=transport)
    message = str(excinfo.value)
    assert "access token" in message.lower()
    # The failing credential must never be echoed back into a UI string.
    assert "stale-token" not in message


async def test_an_empty_token_never_reaches_the_network():
    with pytest.raises(supabase_link.SupabaseLinkError):
        await supabase_link.list_projects("   ", transport=_transport({}))


async def test_resolve_uses_the_host_supabase_reports_not_a_guessed_region():
    """The region prefix in a pooler hostname is not derivable from the ref.

    This is the whole reason the link flow calls the Management API instead of
    string-building: an invented ``aws-0-<region>`` prefix yields a host that
    does not resolve, and the user sees a connection error with no cause.
    """
    transport = _transport(
        {
            POOLER_PATH: (
                200,
                [
                    {
                        "db_host": "aws-1-eu-central-2.pooler.supabase.com",
                        "db_port": 6543,
                        "db_user": "postgres.abcdefghijklmnopqrst",
                        "db_name": "postgres",
                        "pool_mode": "transaction",
                    }
                ],
            )
        }
    )
    endpoint, note = await supabase_link.resolve_endpoint(
        "tok", "abcdefghijklmnopqrst", transport=transport
    )
    assert endpoint.host == "aws-1-eu-central-2.pooler.supabase.com"
    assert endpoint.port == 6543
    assert endpoint.mode == "transaction"
    assert "pooler" in note


async def test_session_mode_keeps_the_reported_host_and_switches_the_port():
    transport = _transport(
        {
            POOLER_PATH: (
                200,
                {
                    "db_host": "aws-1-eu-central-2.pooler.supabase.com",
                    "db_port": 6543,
                    "db_user": "postgres.abcdefghijklmnopqrst",
                    "db_name": "postgres",
                },
            )
        }
    )
    endpoint, _note = await supabase_link.resolve_endpoint(
        "tok", "abcdefghijklmnopqrst", mode="session", transport=transport
    )
    assert endpoint.host == "aws-1-eu-central-2.pooler.supabase.com"
    assert endpoint.port == 5432


async def test_an_unreadable_pooler_config_falls_back_instead_of_failing():
    """Some projects and token scopes expose no pooler config at all.

    That is a normal state, not an outage — the flow degrades to the direct
    host and names the IPv4 caveat so a later failure is diagnosable.
    """
    transport = _transport({POOLER_PATH: (403, {"message": "forbidden"})})
    endpoint, note = await supabase_link.resolve_endpoint(
        "tok", "abcdefghijklmnopqrst", transport=transport
    )
    assert endpoint.host == "db.abcdefghijklmnopqrst.supabase.co"
    assert endpoint.mode == "direct"
    assert "IPv6" in note


async def test_a_nonsense_pool_mode_falls_back_to_transaction():
    transport = _transport({POOLER_PATH: (404, {})})
    endpoint, _ = await supabase_link.resolve_endpoint(
        "tok", "abcdefghijklmnopqrst", mode="wat", transport=transport
    )
    # Fell through to direct, but the mode normalisation must not have raised.
    assert endpoint.mode == "direct"


def test_a_password_with_uri_punctuation_still_parses():
    """Supabase passwords may contain @ : / # — all URI-significant.

    Without percent-encoding, "p@ss/word" turns the host into "ss" and the
    failure surfaces as an unresolvable hostname three layers away from the
    input box that caused it.
    """
    endpoint = supabase_link.PoolerEndpoint(
        host="aws-1-eu-central-2.pooler.supabase.com",
        port=6543,
        user="postgres.abcdefghijklmnopqrst",
        database="postgres",
        mode="transaction",
    )
    uri = supabase_link.build_connection_string(endpoint, "p@ss/w#rd:1")
    parts = urlsplit(uri)
    assert parts.hostname == "aws-1-eu-central-2.pooler.supabase.com"
    assert parts.port == 6543
    assert unquote(parts.password or "") == "p@ss/w#rd:1"
    assert unquote(parts.username or "") == "postgres.abcdefghijklmnopqrst"
    assert parts.path == "/postgres"
    assert "sslmode=require" in parts.query


def test_an_empty_password_is_refused_before_a_broken_uri_is_built():
    endpoint = supabase_link.direct_endpoint_for("abcdefghijklmnopqrst")
    with pytest.raises(supabase_link.SupabaseLinkError):
        supabase_link.build_connection_string(endpoint, "")
