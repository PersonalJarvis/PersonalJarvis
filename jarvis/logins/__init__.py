"""The credential vault: who holds which account, and how those accounts are used.

Two identities live in here, side by side. ``CredentialOwner.USER`` records are
the user's own accounts, used to act on their behalf. ``CredentialOwner.AGENT``
records are accounts the assistant holds in its own name — its own mailbox, its
own number, its own forge account — used to act as itself, with
:mod:`jarvis.logins.identity` holding the details they are registered under.
That separation is the point: an assistant that only ever borrows the user's
logins puts the user's name on everything it does.

Two rules shape every module in here, and neither is negotiable:

1. A password NEVER reaches the language model. The brain may learn *that* a
   login exists, for which service, and under which username — it never sees
   the value. ``store`` holds the record; ``injection`` moves the value into a
   browser script on the way to the harness process, past the model.
2. A password NEVER reaches the user by voice or chat (contract §7, "no keys
   via voice/chat"). It is typed into the desktop section and revealed there on
   an explicit click, nowhere else.
"""

from jarvis.logins.identity import AgentIdentity, IdentityStore
from jarvis.logins.store import (
    Credential,
    CredentialOwner,
    CredentialStore,
    CredentialSummary,
    LoginStatus,
)

__all__ = [
    "AgentIdentity",
    "Credential",
    "CredentialOwner",
    "CredentialStore",
    "CredentialSummary",
    "IdentityStore",
    "LoginStatus",
]
