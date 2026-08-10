"""freedesktop Desktop Entry encoding — both nested escaping layers.

Linux-only in effect, CI-provable everywhere: pure string transforms, no I/O.
Each case pins a rule from the Desktop Entry Specification, because getting one
wrong makes the desktop discard the entry silently while the writer still
reports it as installed and current.
"""

from __future__ import annotations

from jarvis.core.desktop_entry import (
    escape_value,
    exec_argument,
    exec_value,
    read_field,
    reads_as_false,
    reads_as_true,
    unescape_value,
)

# ---- Key-file layer ---------------------------------------------------------


def test_escape_value_encodes_backslash_and_whitespace() -> None:
    assert escape_value(r"/home/u/back\slash") == r"/home/u/back\\slash"
    assert escape_value("a\nb\tc\rd") == "a\\nb\\tc\\rd"


def test_escape_value_escapes_the_backslash_first() -> None:
    """Order matters: a naive newline-first pass would re-escape its own output."""
    assert escape_value("\\n") == "\\\\n"
    assert unescape_value(escape_value("\\n")) == "\\n"


def test_escape_unescape_round_trips() -> None:
    for raw in (
        "/usr/bin/python3",
        r"/opt/My Apps/back\slash/python3",
        "tab\there",
        "percent %f literal",
        "",
    ):
        assert unescape_value(escape_value(raw)) == raw


def test_unescape_decodes_the_space_escape_and_keeps_unknown_ones() -> None:
    assert unescape_value(r"a\sb") == "a b"
    # \" is not a defined key-file escape; passing it through verbatim keeps a
    # foreign file from silently comparing equal to something we wrote.
    assert unescape_value(r"a\"b") == r"a\"b"
    assert unescape_value("trailing\\") == "trailing\\"


# ---- Exec layer -------------------------------------------------------------


def test_exec_argument_leaves_a_plain_token_alone() -> None:
    assert exec_argument("/usr/bin/python3") == "/usr/bin/python3"
    assert exec_argument("-m") == "-m"
    assert exec_argument("jarvis.ui.web.launcher") == "jarvis.ui.web.launcher"


def test_exec_argument_quotes_reserved_characters() -> None:
    assert exec_argument("/opt/My Apps/python3") == '"/opt/My Apps/python3"'
    for reserved in '"\'><~|&;$*?#()`':
        assert exec_argument(f"a{reserved}b").startswith('"')


def test_exec_argument_doubles_a_percent_without_quoting() -> None:
    """``%`` starts a field code — a literal one must be written ``%%``.

    It does not make the argument reserved, so no quotes are added: an unknown
    field code invalidates the whole entry, which is exactly the silent-death
    case this rule prevents.
    """
    assert exec_argument("/home/50%off/python3") == "/home/50%%off/python3"


def test_exec_argument_backslash_escapes_inside_quotes() -> None:
    assert exec_argument('say "hi"') == '"say \\"hi\\""'
    assert exec_argument("cost $5") == '"cost \\$5"'
    assert exec_argument("tick ` mark") == '"tick \\` mark"'


def test_exec_value_needs_four_backslashes_for_a_literal_one() -> None:
    """The spec's own worked example: the Exec layer doubles the backslash and
    the key-file layer doubles that again."""
    assert exec_value(r"/opt/a b/back\slash") == '"/opt/a b/back\\\\\\\\slash"'
    assert exec_value(r"/opt/a b/back\slash").count("\\") == 4


def test_exec_value_renders_program_plus_args() -> None:
    assert exec_value("/usr/bin/python3", ("-m", "jarvis.ui.web.launcher")) == (
        "/usr/bin/python3 -m jarvis.ui.web.launcher"
    )


def test_exec_value_of_an_empty_program_is_empty() -> None:
    """Better an obviously-broken entry than a plausible pair of empty quotes."""
    assert exec_value("", ("-m", "x")) == ""


# ---- Reading ----------------------------------------------------------------

_ENTRY = """\
# a comment
[Desktop Entry]
Type=Application
Name=Personal Jarvis
Exec=/usr/bin/python3 -m jarvis.ui.web.launcher
Path = /home/u/Personal Jarvis
Name[de]=Persoenlicher Jarvis
Hidden=false

[Desktop Action new-window]
Exec=/somewhere/else --new-window
"""


def test_read_field_reads_the_desktop_entry_group() -> None:
    assert read_field(_ENTRY, "Type") == "Application"
    assert read_field(_ENTRY, "Exec") == "/usr/bin/python3 -m jarvis.ui.web.launcher"


def test_read_field_ignores_other_groups() -> None:
    """A ``[Desktop Action …]`` group may legally carry its own ``Exec=``.

    A line-prefix scan answers the drift check with that command instead, and
    then rewrites a perfectly good entry on every single boot.
    """
    assert read_field(_ENTRY, "Exec") != "/somewhere/else --new-window"
    assert read_field(_ENTRY, "Exec", group="Desktop Action new-window") == (
        "/somewhere/else --new-window"
    )


def test_read_field_tolerates_spaces_around_the_equals_sign() -> None:
    assert read_field(_ENTRY, "Path") == "/home/u/Personal Jarvis"


def test_read_field_does_not_match_a_locale_suffixed_key() -> None:
    assert read_field(_ENTRY, "Name") == "Personal Jarvis"


def test_read_field_returns_none_for_a_missing_key() -> None:
    assert read_field(_ENTRY, "StartupWMClass") is None
    assert read_field("", "Exec") is None


def test_read_field_takes_the_last_duplicate_like_gkeyfile() -> None:
    text = "[Desktop Entry]\nHidden=false\nHidden=true\n"
    assert read_field(text, "Hidden") == "true"


def test_read_field_skips_comments() -> None:
    text = "[Desktop Entry]\n#Exec=/commented/out\nExec=/real\n"
    assert read_field(text, "Exec") == "/real"


# ---- Booleans ---------------------------------------------------------------


def test_boolean_readers_treat_a_missing_key_as_unset() -> None:
    """Absent is NOT false: both desktop off-switches default to enabled."""
    assert reads_as_false(None) is False
    assert reads_as_true(None) is False


def test_boolean_readers_are_case_and_space_tolerant() -> None:
    assert reads_as_false("False") is True
    assert reads_as_false(" false ") is True
    assert reads_as_true("TRUE") is True
    assert reads_as_false("true") is False
    assert reads_as_true("0") is False
