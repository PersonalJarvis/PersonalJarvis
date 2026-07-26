"""Keep credentials out of the knowledge base — deterministically, before storage.

Reading "everything a source holds" changed what ingestion is. A folder walk, a
Dropbox and a repository do not contain only documents: they contain `.env`
files, `id_rsa`, `*.pem`, service-account JSON and cloud credentials. Those
would land in a store that a language model reads from and that answers
questions in plain text — so a single sync could turn a private key into
something the assistant helpfully quotes back.

This module is the last gate before storage. It is **deterministic**: regular
expressions and file names, no model call, no judgement. A gate that asks an
LLM whether something is a secret is a gate that leaks on the day the model is
unavailable, slow, or wrong — and it would send the secret to the provider to
ask.

Two layers, because they answer different questions:

* **Withhold by NAME** — a file whose whole purpose is to hold credentials is
  never stored with its content. Redacting line by line would be a bet that the
  patterns below are exhaustive, and for a credential file that bet is not
  worth taking.
* **Redact by SHAPE** — inside ordinary content, individual secrets are
  replaced with a marker naming what was removed. A pasted token in a chat
  message, an access key in a README, a connection string in a note.

Both keep the ITEM. A withheld file still becomes a searchable record of its
own existence — the memory may say "there is a .env in this project" (true and
useful) without saying what is in it. Dropping the item instead would also
break resume: the sync checkpoint is the last item's id.

False positives cost a redaction marker in one paragraph. False negatives cost
a leaked credential. The patterns are therefore written to catch the shapes
whose formats are published and unmistakable, and the generic assignment rule
demands a value that does not look like prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from jarvis.ultrawiki.types import RawItem

__all__ = [
    "SECRET_FILENAMES",
    "ScrubResult",
    "looks_like_credential_file",
    "redact_secrets",
    "scrub_item",
]

#: Exact names whose entire purpose is holding credentials.
_SECRET_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".envrc",
        ".netrc",
        "_netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".htpasswd",
        ".pgpass",
        "credentials",
        "credentials.json",
        "client_secret.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "secring.gpg",
        "shadow",
    }
)

#: Suffixes that mark key material or a credential store.
_SECRET_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pem",
        ".key",
        ".pfx",
        ".p12",
        ".jks",
        ".keystore",
        ".ppk",
        ".kdbx",
        ".asc",
        ".gpg",
        ".pgp",
    }
)

#: Name shapes: `.env.production`, `secrets.yaml`, `service-account-x.json`.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\.env(\..+)?$"),
    re.compile(r"^secrets?\.(ya?ml|json|toml|ini|txt|env)$"),
    re.compile(r"^service[-_]account.*\.json$"),
    re.compile(r".*[-_]credentials?\.(json|ya?ml|ini|txt)$"),
)

#: Public view for the walkers and the UI that want to explain the rule.
SECRET_FILENAMES: frozenset[str] = _SECRET_NAMES

#: Item metadata keys that may carry the file's real name. An attachment, a
#: Dropbox entry and a folder walk each name it differently, and the title is
#: not always the filename.
_NAME_KEYS: tuple[str, ...] = (
    "filename",
    "attachment_filename",
    "path_display",
    "relative_path",
    "path",
)

#: Published credential formats. Each is unmistakable on its own: the prefix
#: and the length are defined by the issuer, so a match is not a guess.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}\b")),
    ("API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b")),
    (
        "signed token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "connection string password",
        re.compile(r"(?<=://)([^\s:/@]+):([^\s:/@]{6,})(?=@)"),
    ),
)

#: `API_KEY=...`, `password: "..."`. The NAME is kept and only the value is
#: replaced, so the record still says a password was configured there.
_ASSIGNMENT = re.compile(
    r"(?i)\b([a-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|"
    r"access[_-]?key|private[_-]?key|auth)[a-z0-9_.-]*)\s*[:=]\s*"
    r"[\"']?([^\s\"',;]{12,})[\"']?"
)

#: A dotted or underscored identifier: `token_store`, `jarvis.brain.tools`,
#: `MAX_RETRIES`. Code is what a developer's corpus is mostly MADE of, so this
#: shape must never be mistaken for a credential. Case-consistent on purpose —
#: identifiers are written in one case, while a generated token mixes them,
#: and `qX7-longEnoughValue123` must stay redactable.
_IDENTIFIER = re.compile(
    r"^([a-z_][a-z0-9_]*([.\-][a-z0-9_]+)*|[A-Z_][A-Z0-9_]*([.\-][A-Z0-9_]+)*)$"
)

#: Words a document writes where a password would go. Every setup guide in
#: existence contains `user:password@host`; redacting the WORD teaches nobody
#: anything and makes the instructions unreadable.
_PASSWORD_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "pwd",
        "secret",
        "token",
        "your_password",
        "yourpassword",
        "changeme",
        "hunter2",
    }
)

#: Characters that mark a path, a call or a URL: never part of a bare secret.
_PATH_PUNCTUATION = "/\\()[]{}<>$\"'`,;|*?"

#: A lowercase dotted tail — `.com`, `.py`, `.apps.googleusercontent.com`. A
#: hostname and a filename both end this way; a generated credential does not.
_DOTTED_TAIL = re.compile(r"\.[a-z]{2,24}$")

#: What documentation writes where a value would go: `abc…`, `abc...`.
_ELISIONS = ("…", "...")

#: Minimum length for a machine-issued credential. Below this, requiring the
#: other shape rules produces more false positives than it prevents leaks.
_MIN_SECRET_CHARS = 16


@dataclass(slots=True)
class ScrubResult:
    """What the gate did to one item, in terms a user can be shown."""

    item: RawItem
    withheld: bool = False
    reason: str = ""
    redactions: int = 0

    @property
    def touched(self) -> bool:
        return self.withheld or self.redactions > 0


def looks_like_credential_file(name: str) -> bool:
    """Whether this file's purpose is holding credentials.

    Judged on the name alone, deliberately: it is decided before anything is
    read, so the rule also applies to a walker that never opens the file.
    """
    cleaned = str(name or "").strip().replace("\\", "/")
    if not cleaned:
        return False
    base = PurePosixPath(cleaned).name.lower()
    if not base:
        return False
    if base in _SECRET_NAMES:
        return True
    if PurePosixPath(base).suffix in _SECRET_SUFFIXES:
        return True
    return any(pattern.match(base) for pattern in _SECRET_PATTERNS)


def _looks_like_secret_value(value: str) -> bool:
    """Whether a labelled value has the SHAPE of a machine-issued credential.

    Deliberately demanding. Measured over 1 500 real files from a developer's
    own disk, the obvious rule — "anything after ``token =``" — produced 513
    matches of which effectively all were code: ``cancel_token=None``,
    ``authorization_url=...``, even a test filename containing "api_key". A
    redactor that eats source and prose damages the knowledge base in a way
    nobody notices until a search comes back empty, so a value has to look
    like what an issuer generates: long, mixed, and neither an identifier nor
    a path.
    """
    candidate = value.strip().strip("\"'")
    if len(candidate) < _MIN_SECRET_CHARS:
        return False
    if any(character in _PATH_PUNCTUATION for character in candidate):
        return False
    if _IDENTIFIER.match(candidate):
        return False  # `token_store`, `jarvis.brain.tools`, `some-flag-name`
    if _DOTTED_TAIL.search(candidate):
        return False  # a hostname or a filename, not a credential
    if any(mark in candidate for mark in _ELISIONS):
        return False  # documentation showing the SHAPE of a value
    has_digit = any(character.isdigit() for character in candidate)
    has_lower = any(character.islower() for character in candidate)
    has_upper = any(character.isupper() for character in candidate)
    # Either the mix a random generator produces, or long enough that nothing
    # a person types by hand reaches it.
    return (has_digit and has_lower and has_upper) or (
        has_digit and len(candidate) >= 32
    )


def redact_secrets(text: str) -> tuple[str, int]:
    """``(text, count)`` with every recognised credential replaced by a marker.

    The marker names the KIND, never the value: the record stays honest about
    what was there without carrying it.
    """
    if not text:
        return ("", 0)
    count = 0

    for label, pattern in _PATTERNS:
        if label == "connection string password":

            def _mask(match: re.Match[str]) -> str:
                password = match.group(2)
                # `postgresql://<user>:<password>@host` is a TEMPLATE — every
                # connection-string example in every document looks like this,
                # and redacting the word "password" out of documentation makes
                # the instructions unreadable for no security gain.
                if (
                    password.startswith(("<", "{", "$", "%"))
                    or password.lower().strip("*") in _PASSWORD_PLACEHOLDERS
                    or any(mark in password for mark in _ELISIONS)
                ):
                    return match.group(0)
                return f"{match.group(1)}:[redacted: password]"

            text, hits = pattern.subn(_mask, text)
            # subn counts every match; only the masked ones were redactions.
            hits = text.count("[redacted: password]")
        else:
            text, hits = pattern.subn(f"[redacted: {label}]", text)
        count += hits

    def _mask_assignment(match: re.Match[str]) -> str:
        nonlocal count
        name, value = match.group(1), match.group(2)
        if not _looks_like_secret_value(value) or value.startswith("[redacted:"):
            return match.group(0)
        count += 1
        return f"{name}=[redacted: value]"

    text = _ASSIGNMENT.sub(_mask_assignment, text)
    return (text, count)


def _candidate_names(item: RawItem) -> list[str]:
    names = [str(item.metadata.get(key) or "") for key in _NAME_KEYS]
    names.append(item.title)
    return [name for name in names if name]


def scrub_item(item: RawItem) -> ScrubResult:
    """The gate every item passes before it is stored.

    A credential FILE keeps its name, its date and its link and loses its
    content; ordinary content keeps everything but the secrets inside it.
    """
    for name in _candidate_names(item):
        if looks_like_credential_file(name):
            reason = "this file holds credentials, so its content is not imported"
            withheld = replace(
                item,
                body=f"File: {PurePosixPath(name.replace(chr(92), '/')).name}\n"
                f"[content withheld: {reason}]",
                metadata={
                    **item.metadata,
                    "secret_withheld": True,
                    "content_missing": True,
                    "content_missing_reason": reason,
                },
            )
            return ScrubResult(item=withheld, withheld=True, reason=reason)

    cleaned, count = redact_secrets(item.body)
    if not count:
        return ScrubResult(item=item)
    return ScrubResult(
        item=replace(
            item,
            body=cleaned,
            metadata={**item.metadata, "secret_redactions": count},
        ),
        redactions=count,
    )
