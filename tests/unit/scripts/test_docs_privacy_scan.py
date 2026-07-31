"""Regression tests for the public, generic documentation privacy rules."""

from scripts.ci import docs_privacy_scan


def test_private_home_prefix_is_masked_without_consuming_the_line() -> None:
    rules, emails = docs_privacy_scan.load_manifest()
    source = r"Open C:\Users" + r"\Administrator\project\README.md next."

    assert docs_privacy_scan.fix_text(source, rules, emails) == (
        r"Open <USER_HOME>\project\README.md next."
    )


def test_generic_home_examples_and_snapshot_ids_are_not_flagged() -> None:
    rules, emails = docs_privacy_scan.load_manifest()
    source = (
        r"C:\Users\Developer\repo C:/Users/Example/repo "
        "claude-opus-4-7-20251022 wip/cross-platform-20260529"
    )

    assert docs_privacy_scan.scan_text(source, rules, emails) == []


def test_consumer_emails_and_google_project_id_are_flagged() -> None:
    rules, emails = docs_privacy_scan.load_manifest()
    source = "\n".join(
        (
            "alice" + "@" + "gmail.com",
            "bob" + "@" + "yahoo.com",
            "casey" + "@" + "protonmail.com",
            "dana" + "@" + "proton.me",
            "uses gen-lang-client-" + "1234567890",
        )
    )

    hits = docs_privacy_scan.scan_text(source, rules, emails)

    assert len(hits) == 5
    assert docs_privacy_scan.fix_text(source, rules, emails) == (
        "maintainer@example.com\nmaintainer@example.com\nmaintainer@example.com\n"
        "maintainer@example.com\nuses <CLOUD_PROJECT_ID>"
    )
