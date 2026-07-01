"""Tests for path_guard — blocklist for protected paths."""
from __future__ import annotations

import pytest

from jarvis.missions.safety.path_guard import (
    DEFAULT_BLOCKED_GLOBS,
    check_prompt_for_blocked_paths,
    filter_diff_paths,
    is_blocked,
)


# --- is_blocked: SSH ---


def test_ssh_id_rsa_blocked() -> None:
    assert is_blocked("/home/user/.ssh/id_rsa") is True


def test_ssh_id_rsa_pub_blocked() -> None:
    assert is_blocked("/home/user/.ssh/id_rsa.pub") is True


def test_ssh_id_ed25519_blocked() -> None:
    assert is_blocked("~/.ssh/id_ed25519") is True


def test_ssh_authorized_keys_blocked() -> None:
    assert is_blocked("/etc/ssh/authorized_keys") is True


def test_ssh_dir_blocked() -> None:
    assert is_blocked("/home/x/.ssh") is True


# --- AWS ---


def test_aws_credentials_blocked() -> None:
    assert is_blocked("/home/user/.aws/credentials") is True


def test_aws_config_blocked() -> None:
    assert is_blocked("/home/user/.aws/config") is True


# --- Env files ---


def test_dotenv_blocked() -> None:
    assert is_blocked("/project/.env") is True


def test_dotenv_production_blocked() -> None:
    assert is_blocked("./.env.production") is True


def test_env_substring_in_filename_not_blocked() -> None:
    """`environments.py` must not match — we only want `.env*` files."""
    assert is_blocked("/project/environments.py") is False


# --- Cert/Keys ---


def test_pem_blocked() -> None:
    assert is_blocked("/etc/ssl/cert.pem") is True


def test_key_blocked() -> None:
    assert is_blocked("/var/keys/server.key") is True


# --- Clean files NOT blocked ---


def test_python_file_not_blocked() -> None:
    assert is_blocked("/project/src/main.py") is False


def test_test_file_not_blocked() -> None:
    assert is_blocked("/project/tests/test_x.py") is False


def test_readme_not_blocked() -> None:
    assert is_blocked("README.md") is False


def test_envvar_in_path_not_blocked() -> None:
    """`environment-config.yaml` must NOT match — only exact `.env*` files."""
    assert is_blocked("config/environment-config.yaml") is False


# --- Windows-style paths ---


def test_windows_backslash_path_normalized() -> None:
    assert is_blocked(r"C:\Users\Admin\.ssh\id_rsa") is True


def test_windows_dotenv_blocked() -> None:
    assert is_blocked(r"C:\proj\.env") is True


# --- Extra globs (config-driven) ---


def test_extra_globs_additive() -> None:
    extras = ("**/super-secret.json",)
    assert is_blocked("/var/super-secret.json", extra_globs=extras) is True
    assert is_blocked("/var/regular.json", extra_globs=extras) is False


def test_extra_globs_dont_break_defaults() -> None:
    extras = ("**/foo.bar",)
    # Defaults still active
    assert is_blocked("~/.ssh/id_rsa", extra_globs=extras) is True


# --- filter_diff_paths ---


_SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+# new line
 print('hi')
diff --git a/.env b/.env
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/.env
@@ -0,0 +1,1 @@
+SECRET=val
diff --git a/README.md b/README.md
index xxx..yyy 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""


def test_filter_diff_finds_blocked_only() -> None:
    blocked = filter_diff_paths(_SAMPLE_DIFF)
    assert blocked == [".env"]


def test_filter_empty_diff() -> None:
    assert filter_diff_paths("") == []


def test_filter_diff_with_no_blocked() -> None:
    diff = """\
diff --git a/src/x.py b/src/x.py
index 0..1 100644
"""
    assert filter_diff_paths(diff) == []


def test_filter_diff_extra_globs() -> None:
    diff = """\
diff --git a/secret.json b/secret.json
new file mode 100644
"""
    assert filter_diff_paths(diff, extra_globs=("**/secret.json",)) == ["secret.json"]


def test_filter_diff_rename_both_paths_checked() -> None:
    diff = """\
diff --git a/old/config.py b/new/.env
rename from old/config.py
rename to new/.env
"""
    blocked = filter_diff_paths(diff)
    # Beide a/ und b/ checken — .env trifft
    assert ".env" in [p.rsplit("/", 1)[-1] for p in blocked]


# --- check_prompt_for_blocked_paths ---


def test_prompt_with_explicit_ssh_path() -> None:
    found = check_prompt_for_blocked_paths("Bitte zeig mir ~/.ssh/id_rsa an.")
    assert any(".ssh" in p for p in found) or any("id_rsa" in p for p in found)


def test_prompt_with_dotenv_path() -> None:
    found = check_prompt_for_blocked_paths("Lies das .env file im /project/ Ordner")
    # `.env` alone does NOT match (no slash/dot prefix as token start) —
    # we want to avoid false positives on phrases like "the env variable".
    # If someone explicitly writes "/project/.env", that should match.
    found2 = check_prompt_for_blocked_paths("zeig /project/.env")
    assert any(".env" in p for p in found2)


def test_prompt_without_paths_no_match() -> None:
    """A harmless prompt must produce NO matches."""
    found = check_prompt_for_blocked_paths("Schreibe eine Funktion is_palindrome")
    assert found == []


def test_prompt_mentioning_env_word_not_match() -> None:
    """`die env Variable` must NOT match as a path — only real paths."""
    found = check_prompt_for_blocked_paths("Setze die env Variable auf foo")
    assert found == []


def test_empty_prompt_returns_empty() -> None:
    assert check_prompt_for_blocked_paths("") == []


# --- DEFAULT_BLOCKED_GLOBS Sanity ---


def test_default_globs_include_critical_paths() -> None:
    """Verifies that the top risks are in DEFAULT_BLOCKED_GLOBS."""
    blob = " ".join(DEFAULT_BLOCKED_GLOBS)
    assert ".ssh" in blob
    assert ".aws" in blob
    assert ".env" in blob
    assert "id_rsa" in blob
    assert ".pem" in blob
