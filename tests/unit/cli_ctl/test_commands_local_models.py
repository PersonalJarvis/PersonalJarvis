"""`jarvis local-models ...` — the "Local models" section from a terminal.

Every command must hit the exact mounted route with the exact body, the
provider must default to ``ollama`` and follow ``--provider``, and the three
destructive commands (unload, delete, server stop) must refuse without
``--yes`` while the reversible writes (roles, options, HF switch) just run.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()
_BASE = "/api/providers/ollama/local-models"


def _one(capture_api) -> dict:
    assert len(capture_api["calls"]) == 1, capture_api["calls"]
    return capture_api["calls"][0]


# ----------------------------------------------------------------------
# roles
# ----------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["roles"], ["roles", "list"]])
def test_roles_lists_bare_and_explicit(capture_api, argv) -> None:
    res = runner.invoke(app, ["local-models", *argv])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("GET", f"{_BASE}/roles")


def test_roles_set_puts_model_without_yes(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "roles", "set", "deep", "qwen3:32b"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("PUT", f"{_BASE}/roles/deep")
    assert call["body"] == {"model": "qwen3:32b"}


def test_roles_set_empty_model_hands_back_to_discovery(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "roles", "set", "chat"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["body"] == {"model": ""}


def test_provider_option_changes_the_prefix(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "roles", "--provider", "llamacpp"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["path"] == "/api/providers/llamacpp/local-models/roles"


# ----------------------------------------------------------------------
# models
# ----------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["models"], ["models", "list"]])
def test_models_lists_inventory(capture_api, argv) -> None:
    res = runner.invoke(app, ["local-models", *argv])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("GET", f"{_BASE}/inventory")


def test_models_show_keeps_the_tag_colon(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "models", "show", "qwen3:32b"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["path"] == f"{_BASE}/inventory/qwen3:32b"


def test_models_unload_requires_yes(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "models", "unload", "qwen3:32b"])
    assert res.exit_code == 1
    assert capture_api["calls"] == []


def test_models_unload_with_yes(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "models", "unload", "qwen3:32b", "--yes"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/inventory/qwen3:32b/unload")


def test_models_delete_requires_yes(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "models", "delete", "old:7b"])
    assert res.exit_code == 1
    assert capture_api["calls"] == []


def test_models_delete_with_reassign_and_yes(capture_api) -> None:
    res = runner.invoke(
        app,
        ["local-models", "models", "delete", "old:7b", "--reassign", "qwen3:32b", "--yes"],
    )
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("DELETE", f"{_BASE}/inventory/old:7b")
    assert call["query"] == {"reassign": "qwen3:32b"}


def test_models_delete_without_reassign_sends_no_query(capture_api) -> None:
    runner.invoke(app, ["local-models", "models", "delete", "old:7b", "--yes"])
    assert _one(capture_api)["query"] == {}


def test_models_delete_conflict_exits_nonzero(capture_api) -> None:
    capture_api["routes"][("DELETE", f"{_BASE}/inventory/old:7b")] = (
        409,
        {"detail": "old:7b is still the pick for chat."},
    )
    res = runner.invoke(app, ["local-models", "models", "delete", "old:7b", "--yes"])
    assert res.exit_code == 1


# ----------------------------------------------------------------------
# options
# ----------------------------------------------------------------------


def test_options_get(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "options", "get", "qwen3:32b"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("GET", f"{_BASE}/models/qwen3:32b/options")


def test_options_set_types_the_pairs(capture_api) -> None:
    res = runner.invoke(
        app,
        [
            "local-models",
            "options",
            "set",
            "qwen3:32b",
            "num_ctx=16384",
            "temperature=0.2",
            "think=false",
            "keep_alive=10m",
            'stop=["</s>"]',
        ],
    )
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("PUT", f"{_BASE}/models/qwen3:32b/options")
    assert call["body"] == {
        "num_ctx": 16384,
        "temperature": 0.2,
        "think": False,
        "keep_alive": "10m",
        "stop": ["</s>"],
    }


def test_options_set_rejects_a_bare_word(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "options", "set", "qwen3:32b", "num_ctx"])
    assert res.exit_code != 0
    assert capture_api["calls"] == []


def test_options_clear_is_reversible(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "options", "clear", "qwen3:32b"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("DELETE", f"{_BASE}/models/qwen3:32b/options")


def test_options_suggest(capture_api) -> None:
    runner.invoke(app, ["local-models", "options", "suggest", "qwen3:32b"])
    assert _one(capture_api)["path"] == f"{_BASE}/models/qwen3:32b/suggested-options"


# ----------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------


def test_catalog_search_params(capture_api) -> None:
    res = runner.invoke(
        app,
        ["local-models", "catalog", "search", "vision", "--capability", "vision", "--limit", "5"],
    )
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("GET", f"{_BASE}/catalog")
    assert call["query"] == {"q": "vision", "sort": "popular", "capability": "vision", "limit": "5"}


def test_catalog_tags_and_recommended(capture_api) -> None:
    runner.invoke(app, ["local-models", "catalog", "tags", "qwen3"])
    runner.invoke(app, ["local-models", "catalog", "recommended"])
    paths = [c["path"] for c in capture_api["calls"]]
    assert paths == [f"{_BASE}/catalog/qwen3/tags", f"{_BASE}/catalog/recommended"]


# ----------------------------------------------------------------------
# hf
# ----------------------------------------------------------------------


def test_hf_search(capture_api) -> None:
    res = runner.invoke(
        app, ["local-models", "hf", "search", "llama gguf", "--sort", "lastModified"]
    )
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert call["path"] == f"{_BASE}/hf/search"
    assert call["query"] == {"q": "llama gguf", "sort": "lastModified", "limit": "30"}


def test_hf_files(capture_api) -> None:
    runner.invoke(app, ["local-models", "hf", "files", "bartowski", "Llama-3.1-8B-GGUF"])
    assert _one(capture_api)["path"] == f"{_BASE}/hf/bartowski/Llama-3.1-8B-GGUF/files"


def test_hf_pull_body(capture_api) -> None:
    res = runner.invoke(
        app,
        ["local-models", "hf", "pull", "bartowski", "Llama-3.1-8B-GGUF", "--quant", "Q4_K_M"],
    )
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/hf/pull")
    assert call["body"] == {"user": "bartowski", "repo": "Llama-3.1-8B-GGUF", "quant": "Q4_K_M"}


def test_hf_enable_shows_then_flips(capture_api) -> None:
    runner.invoke(app, ["local-models", "hf", "enable"])
    runner.invoke(app, ["local-models", "hf", "enable", "on"])
    runner.invoke(app, ["local-models", "hf", "enable", "off"])
    calls = capture_api["calls"]
    assert [(c["method"], c["body"]) for c in calls] == [
        ("GET", None),
        ("PUT", {"enabled": True}),
        ("PUT", {"enabled": False}),
    ]
    assert all(c["path"] == f"{_BASE}/hf/enabled" for c in calls)


def test_hf_enable_rejects_other_words(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "hf", "enable", "maybe"])
    assert res.exit_code != 0
    assert capture_api["calls"] == []


# ----------------------------------------------------------------------
# server
# ----------------------------------------------------------------------


def test_server_status(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "server", "status"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["path"] == f"{_BASE}/server"


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_server_stop_requires_yes_or_dry_run(capture_api, extra) -> None:
    res = runner.invoke(app, ["local-models", "server", "stop", *extra])
    assert capture_api["calls"] == []
    assert res.exit_code == (0 if extra else 1)


def test_server_stop_with_yes(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "server", "stop", "--yes"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/server/stop")


def test_server_test_body(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "server", "test", "http://127.0.0.1:11434"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/server/test")
    assert call["body"] == {"base_url": "http://127.0.0.1:11434"}


def test_server_log_and_env_guide(capture_api) -> None:
    runner.invoke(app, ["local-models", "server", "log", "--lines", "80"])
    runner.invoke(app, ["local-models", "server", "env-guide", "--os", "linux"])
    runner.invoke(app, ["local-models", "server", "env-guide"])
    calls = capture_api["calls"]
    assert calls[0]["path"] == f"{_BASE}/server/log" and calls[0]["query"] == {"lines": "80"}
    assert calls[1]["path"] == f"{_BASE}/server/env-guide" and calls[1]["query"] == {"os": "linux"}
    assert calls[2]["query"] == {}
