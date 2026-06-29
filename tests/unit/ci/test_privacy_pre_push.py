"""Tests for the versioned pre-push privacy gate (Wave 2, layer 1 of 3).

The gate lives in scripts/ci/ (not an importable package), so we add that
directory to sys.path before importing — mirroring how CI/the git hook run the
scripts directly with `python scripts/ci/<name>.py`.

These are pure-function tests plus main()-orchestration tests with the git/IO
wrappers monkeypatched, so the suite stays hermetic (no real network/git/push).

The secret patterns + forbidden basenames are imported the SAME way the gate
imports them (from the ship skill's strip_and_scan.py via importlib), so the
test reflects the real, battle-tested regexes rather than a re-typed copy.

NOTE: no real private email appears anywhere in this file. The gate reads the
block-set from git config; offenders_from_log takes it as an argument, so the
tests use fake `.invalid` stand-ins.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_CI = _REPO_ROOT / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

import privacy_pre_push as gate  # noqa: E402


# --------------------------------------------------------------------------- #
# Import SECRET_PATTERNS / FORBIDDEN_BASENAMES the real way (from the ship skill)
# --------------------------------------------------------------------------- #
def _load_strip_and_scan():
    path = (
        _REPO_ROOT
        / ".claude"
        / "skills"
        / "ship-public-release"
        / "scripts"
        / "strip_and_scan.py"
    )
    spec = importlib.util.spec_from_file_location("strip_and_scan_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SAS = _load_strip_and_scan()
SECRET_PATTERNS = _SAS.SECRET_PATTERNS
FORBIDDEN_BASENAMES = set(_SAS.FORBIDDEN_BASENAMES)
_COMPILED = {name: re.compile(p) for name, p in SECRET_PATTERNS.items()}

# Fake stand-ins — the REAL private emails are never hardcoded in a tracked file
# (they live in git config `privacy.private-email`). offenders_from_log takes the
# block-set as an argument, so the tests can use arbitrary fakes.
_NOREPLY = "12345+maintainer@users.noreply.github.com"
_PRIV_A = "private.one@example.invalid"
_PRIV_B = "private.two@example.invalid"
_PRIV = {_PRIV_A, _PRIV_B}


# --------------------------------------------------------------------------- #
# target_is_public
# --------------------------------------------------------------------------- #
class TestTargetIsPublic:
    def test_remote_name_public_blocks(self):
        assert gate.target_is_public(
            "public", "https://github.com/PersonalJarvis/PersonalJarvis.git"
        )

    def test_origin_with_public_url_blocks(self):
        # Even if the remote is *named* origin, a public URL must be caught.
        assert gate.target_is_public(
            "origin", "https://github.com/PersonalJarvis/PersonalJarvis.git"
        )

    def test_private_work_repo_is_not_public(self):
        assert not gate.target_is_public(
            "origin", "https://github.com/example-org/private-work.git"
        )

    def test_url_match_is_case_insensitive(self):
        assert gate.target_is_public(
            "origin", "git@github.com:PERSONALJARVIS/PERSONALJARVIS.git"
        )
        assert gate.target_is_public(
            "origin", "https://github.com/personaljarvis/personaljarvis"
        )

    def test_empty_inputs_are_not_public(self):
        assert not gate.target_is_public("", "")


# --------------------------------------------------------------------------- #
# offenders_from_log  (takes the private-email block-set as an argument)
# --------------------------------------------------------------------------- #
class TestOffendersFromLog:
    def test_private_author_email_is_offender(self):
        log = f"abc123\t{_PRIV_A}\t{_NOREPLY}"
        offenders = gate.offenders_from_log(log, _PRIV)
        assert len(offenders) == 1
        assert offenders[0]["sha"] == "abc123"
        assert offenders[0]["email"] == _PRIV_A
        assert offenders[0]["role"] == "author"

    def test_noreply_only_is_clean(self):
        log = f"def456\t{_NOREPLY}\t{_NOREPLY}"
        assert gate.offenders_from_log(log, _PRIV) == []

    def test_private_committer_with_noreply_author_is_offender(self):
        log = f"ghi789\t{_NOREPLY}\t{_PRIV_B}"
        offenders = gate.offenders_from_log(log, _PRIV)
        assert len(offenders) == 1
        assert offenders[0]["sha"] == "ghi789"
        assert offenders[0]["email"] == _PRIV_B
        assert offenders[0]["role"] == "committer"

    def test_both_private_records_both_roles(self):
        log = f"jkl012\t{_PRIV_A}\t{_PRIV_B}"
        offenders = gate.offenders_from_log(log, _PRIV)
        roles = sorted(o["role"] for o in offenders)
        assert roles == ["author", "committer"]
        assert all(o["sha"] == "jkl012" for o in offenders)

    def test_multiple_lines_mixed(self):
        log = "\n".join(
            [
                f"aaa\t{_NOREPLY}\t{_NOREPLY}",  # clean
                f"bbb\t{_PRIV_A}\t{_NOREPLY}",   # offender (author)
                "",                              # blank line tolerated
                f"ccc\t{_NOREPLY}\t{_PRIV_B}",   # offender (committer)
            ]
        )
        offenders = gate.offenders_from_log(log, _PRIV)
        shas = sorted(o["sha"] for o in offenders)
        assert shas == ["bbb", "ccc"]

    def test_empty_log_is_clean(self):
        assert gate.offenders_from_log("", _PRIV) == []

    def test_uppercased_private_email_is_caught(self):
        # git emits the email verbatim; a deliberately-cased address must still
        # be caught (case-insensitive membership).
        log = f"mno345\t{_PRIV_A.upper()}\t{_NOREPLY}"
        offenders = gate.offenders_from_log(log, _PRIV)
        assert len(offenders) == 1
        assert offenders[0]["role"] == "author"

    def test_empty_block_set_never_offends(self):
        # No configured private emails -> identity check is a no-op.
        log = f"xyz999\t{_PRIV_A}\t{_PRIV_B}"
        assert gate.offenders_from_log(log, set()) == []


# --------------------------------------------------------------------------- #
# forbidden_file
# --------------------------------------------------------------------------- #
class TestForbiddenFile:
    def test_env_is_forbidden(self):
        assert gate.forbidden_file(".env", FORBIDDEN_BASENAMES)

    def test_jarvis_toml_is_forbidden(self):
        assert gate.forbidden_file("jarvis.toml", FORBIDDEN_BASENAMES)

    def test_mcp_json_is_forbidden(self):
        assert gate.forbidden_file("mcp.json", FORBIDDEN_BASENAMES)

    def test_main_py_is_allowed(self):
        assert not gate.forbidden_file("main.py", FORBIDDEN_BASENAMES)


# --------------------------------------------------------------------------- #
# scan_text_for_secrets
# --------------------------------------------------------------------------- #
# A value that matches the real openai_legacy pattern: sk- + 48 alnum chars.
_REAL_OPENAI = "sk-" + ("A" * 48)
# A value that matches the real github_token pattern: ghp_ + 36 alnum chars.
_REAL_GITHUB = "ghp_" + ("b" * 36)


class TestScanTextForSecrets:
    def test_real_openai_key_is_found(self):
        text = f"OPENAI_API_KEY = '{_REAL_OPENAI}'\n"
        findings = gate.scan_text_for_secrets("config.py", text, _COMPILED, set())
        assert len(findings) == 1
        assert findings[0]["path"] == "config.py"
        assert findings[0]["value"] == _REAL_OPENAI
        assert findings[0]["pattern"] == "openai_legacy"

    def test_real_github_token_is_found(self):
        text = f"token={_REAL_GITHUB}\n"
        findings = gate.scan_text_for_secrets("ci.yml", text, _COMPILED, set())
        assert len(findings) == 1
        assert findings[0]["value"] == _REAL_GITHUB

    def test_allowlisted_value_path_pair_is_suppressed(self):
        text = f"OPENAI_API_KEY = '{_REAL_OPENAI}'\n"
        allow = {(_REAL_OPENAI, "config.py")}
        findings = gate.scan_text_for_secrets("config.py", text, _COMPILED, allow)
        assert findings == []

    def test_allowlist_is_path_specific(self):
        # Same value, but allowlisted only for a DIFFERENT path -> still flagged.
        text = f"OPENAI_API_KEY = '{_REAL_OPENAI}'\n"
        allow = {(_REAL_OPENAI, "other/file.py")}
        findings = gate.scan_text_for_secrets("config.py", text, _COMPILED, allow)
        assert len(findings) == 1

    def test_clean_text_yields_nothing(self):
        text = "def add(a, b):\n    return a + b\n"
        assert gate.scan_text_for_secrets("math.py", text, _COMPILED, set()) == []

    def test_empty_compiled_patterns_yields_nothing(self):
        text = f"OPENAI_API_KEY = '{_REAL_OPENAI}'\n"
        assert gate.scan_text_for_secrets("config.py", text, {}, set()) == []


# --------------------------------------------------------------------------- #
# main() orchestration — git/IO monkeypatched so the suite stays hermetic
# --------------------------------------------------------------------------- #
_NONZERO_A = "1" * 40
_NONZERO_B = "2" * 40
_ALL_ZERO = "0" * 40


class TestMain:
    def test_public_target_blocks_before_reading_stdin(self):
        class _Boom:
            def read(self):  # must never be reached for a public target
                raise AssertionError("stdin must not be read for a public target")

        rc = gate.main(
            ["prog", "public", "https://github.com/PersonalJarvis/PersonalJarvis.git"],
            _Boom(),
        )
        assert rc == 1

    def test_clean_private_push_allows(self, monkeypatch):
        monkeypatch.setattr(gate, "_git", lambda *a: f"sha1\t{_NOREPLY}\t{_NOREPLY}\n")
        monkeypatch.setattr(
            gate, "load_secret_scanner", lambda root: (None, set(), set())
        )
        monkeypatch.setattr(gate, "load_private_emails", lambda: _PRIV)
        stdin = io.StringIO(
            f"refs/heads/main {_NONZERO_A} refs/heads/main {_NONZERO_B}\n"
        )
        rc = gate.main(
            ["prog", "origin", "https://github.com/example-org/private-work.git"],
            stdin,
        )
        assert rc == 0

    def test_private_email_commit_blocks(self, monkeypatch):
        monkeypatch.setattr(gate, "_git", lambda *a: f"sha1\t{_PRIV_A}\t{_NOREPLY}\n")
        monkeypatch.setattr(
            gate, "load_secret_scanner", lambda root: (None, set(), set())
        )
        monkeypatch.setattr(gate, "load_private_emails", lambda: _PRIV)
        stdin = io.StringIO(
            f"refs/heads/main {_NONZERO_A} refs/heads/main {_NONZERO_B}\n"
        )
        rc = gate.main(["prog", "origin", "url"], stdin)
        assert rc == 1

    def test_branch_deletion_is_skipped(self, monkeypatch):
        called = {"git": False}

        def _fake_git(*a):
            called["git"] = True
            return ""

        monkeypatch.setattr(gate, "_git", _fake_git)
        monkeypatch.setattr(
            gate, "load_secret_scanner", lambda root: (None, set(), set())
        )
        monkeypatch.setattr(gate, "load_private_emails", lambda: _PRIV)
        stdin = io.StringIO(f"(delete) {_ALL_ZERO} refs/heads/old {_ALL_ZERO}\n")
        rc = gate.main(["prog", "origin", "url"], stdin)
        assert rc == 0
        assert called["git"] is False  # deletions are inspected by no git call

    def test_secret_in_pushed_file_blocks(self, monkeypatch):
        monkeypatch.setattr(gate, "_git", lambda *a: f"sha1\t{_NOREPLY}\t{_NOREPLY}\n")
        monkeypatch.setattr(
            gate, "load_secret_scanner", lambda root: (_COMPILED, set(), set())
        )
        monkeypatch.setattr(gate, "load_private_emails", lambda: _PRIV)
        monkeypatch.setattr(
            gate,
            "pushed_text_files",
            lambda base, head: [("config.py", f"KEY='{_REAL_OPENAI}'\n")],
        )
        stdin = io.StringIO(
            f"refs/heads/main {_NONZERO_A} refs/heads/main {_NONZERO_B}\n"
        )
        rc = gate.main(["prog", "origin", "url"], stdin)
        assert rc == 1

    def test_forbidden_file_in_push_blocks(self, monkeypatch):
        monkeypatch.setattr(gate, "_git", lambda *a: f"sha1\t{_NOREPLY}\t{_NOREPLY}\n")
        monkeypatch.setattr(
            gate, "load_secret_scanner", lambda root: ({}, {".env"}, set())
        )
        monkeypatch.setattr(gate, "load_private_emails", lambda: _PRIV)
        monkeypatch.setattr(
            gate, "pushed_text_files", lambda base, head: [(".env", "SECRET=1\n")]
        )
        stdin = io.StringIO(
            f"refs/heads/main {_NONZERO_A} refs/heads/main {_NONZERO_B}\n"
        )
        rc = gate.main(["prog", "origin", "url"], stdin)
        assert rc == 1
