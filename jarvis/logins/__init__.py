"""The login vault: the user's own website credentials, and how they are used.

Two rules shape every module in here, and neither is negotiable:

1. A password NEVER reaches the language model. The brain may learn *that* a
   login exists, for which service, and under which username — it never sees
   the value. ``store`` holds the record; ``injection`` moves the value into a
   browser script on the way to the harness process, past the model.
2. A password NEVER reaches the user by voice or chat (contract §7, "no keys
   via voice/chat"). It is typed into the desktop section and revealed there on
   an explicit click, nowhere else.
"""

from jarvis.logins.store import (
    Credential,
    CredentialStore,
    CredentialSummary,
    LoginStatus,
)

__all__ = [
    "Credential",
    "CredentialStore",
    "CredentialSummary",
    "LoginStatus",
]
