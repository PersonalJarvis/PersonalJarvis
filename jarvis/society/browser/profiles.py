"""Durable browser identities and explicit sharing, separate from website secrets."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
 domains TEXT NOT NULL, token_hash TEXT NOT NULL DEFAULT '',
 installation_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
 agent_id TEXT PRIMARY KEY, mode TEXT NOT NULL, profile_id TEXT
);
CREATE TABLE IF NOT EXISTS profile_sources (
 profile_id TEXT PRIMARY KEY, source_agent_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS pairing (
 code_hash TEXT PRIMARY KEY, profile_id TEXT NOT NULL UNIQUE, expires REAL NOT NULL
);
"""


def checked_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("Invalid browser identity")
    return value


def normalize_domains(values: list[str]) -> list[str]:
    """Accept explicit public DNS names, never wildcard or local-address access."""
    result = set()
    if len(values) > 100:
        raise ValueError("Too many browser domains")
    for value in values:
        host = value.strip().lower().rstrip(".").encode("idna").decode("ascii")
        if (
            len(host) > 253
            or "." not in host
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                for p in host.split(".")
            )
            or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
        ):
            raise ValueError("Use a public website domain such as x.com")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # A public DNS name intentionally does not parse as an IP literal.
            result.add(host)
        else:
            raise ValueError("Browser domains must be public DNS names")
    return sorted(result)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProfileBinding:
    key: str
    kind: str
    profile_id: str | None
    path: Path | None
    domains: tuple[str, ...]
    label: str

    @property
    def access_key(self) -> tuple:
        """Metadata labels do not change an account or its allowed operations."""
        return self.key, self.kind, self.path, self.domains


class BrowserProfiles:
    """Small transactional registry; construction performs no IO on startup."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "society" / "browser-profiles.sqlite"

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "allowed_domains": json.loads(row["domains"]),
        }

    @staticmethod
    def _require(db: sqlite3.Connection, profile_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM profiles WHERE id=?", (checked_id(profile_id),)).fetchone()
        if row is None:
            raise ValueError("Browser profile not found; choose a profile explicitly")
        return row

    @staticmethod
    def _default(db: sqlite3.Connection) -> str | None:
        row = db.execute("SELECT value FROM settings WHERE key='default_profile'").fetchone()
        return row[0] if row else None

    @staticmethod
    def _setting(db: sqlite3.Connection, key: str) -> str | None:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _initialized(db: sqlite3.Connection) -> None:
        db.execute("INSERT OR REPLACE INTO settings VALUES('shared_default_initialized','1')")
        db.execute("DELETE FROM settings WHERE key='pending_shared_source'")

    @staticmethod
    def _has_configuration(db: sqlite3.Connection) -> bool:
        # Older selected-only configurations predate the initialization marker.
        return bool(
            db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone()
            or db.execute("SELECT 1 FROM bindings LIMIT 1").fetchone()
        )

    def _source_folder(self, source_agent_id: str) -> Path:
        """Validate owned storage without opening cookies or accepting arbitrary paths."""
        aid = checked_id(source_agent_id)
        root = self.data_dir.resolve() / "society"
        folder = root / aid / "browser-profile"
        try:
            for component in (root, root / aid, folder):
                info = component.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Shared browser storage cannot use a link or junction")
            if not folder.is_dir() or not folder.resolve().is_relative_to(root.resolve()):
                raise ValueError("The agent's browser profile is not an owned directory")
        except OSError as exc:
            raise ValueError("The agent's existing browser profile is unavailable") from exc
        # Match the original binding spelling as well as its physical location.
        return self.data_dir / "society" / aid / "browser-profile"

    def queue_share_all_from_agent(self, source_agent_id: str) -> None:
        """Stage adoption for a capable runtime; older runtimes ignore this hint."""
        aid = checked_id(source_agent_id)
        self._source_folder(aid)
        with self._db() as db:
            if self._default(db) is not None:
                raise ValueError("A shared browser profile is already configured")
            if db.execute(
                "SELECT 1 FROM profile_sources WHERE source_agent_id=?", (aid,)
            ).fetchone():
                raise ValueError("This agent's original browser profile was already shared")
            db.execute("INSERT OR REPLACE INTO settings VALUES('pending_shared_source',?)", (aid,))

    def needs_shared_default(self) -> bool:
        """A read used to avoid locking active controls once setup is settled."""
        with self._db() as db:
            return self._default(db) is None and bool(
                self._setting(db, "pending_shared_source")
                or (
                    self._setting(db, "shared_default_initialized") is None
                    and not self._has_configuration(db)
                )
            )

    def ensure_shared_default(
        self,
        agent: Any,
        *,
        occupied: tuple[tuple[str, ProfileBinding], ...] = (),
    ) -> bool:
        """Initialize shared storage once, or consume an explicitly queued adoption."""
        aid = checked_id(agent.agent_id)
        with self._db() as db:
            # Missing default rows and revoked default IDs have different meanings.
            # An existing choice, including a tombstone, is never replaced here.
            if self._default(db) is not None:
                return False
            pending = self._setting(db, "pending_shared_source")
            if not pending and (
                self._setting(db, "shared_default_initialized") is not None
                or self._has_configuration(db)
            ):
                return False
            row = db.execute("SELECT mode FROM bindings WHERE agent_id=?", (aid,)).fetchone()
            if not pending and (
                (row is not None and row["mode"] != "inherit")
                or str(getattr(agent, "browser_mode", "own")) == "attach"
            ):
                return False
            source = checked_id(pending) if pending else None
            if source is None and (self.data_dir / "society" / aid / "browser-profile").exists():
                source = aid
            path = self._source_folder(source) if source else None
            if source and db.execute(
                "SELECT 1 FROM profile_sources WHERE source_agent_id=?", (source,)
            ).fetchone():
                raise ValueError("This agent's original browser profile was already shared")
            # No writes precede this check: a busy account leaves queued intent intact.
            for other_id, binding in occupied:
                if not pending:
                    override = db.execute(
                        "SELECT mode FROM bindings WHERE agent_id=?", (checked_id(other_id),)
                    ).fetchone()
                    if override is not None and override["mode"] != "inherit":
                        continue
                if not (
                    source
                    and binding.kind == "managed"
                    and binding.key == f"agent-{source}"
                    and binding.path == path
                ):
                    raise RuntimeError("Finish the other browser session before sharing its logins")
            pid = uuid4().hex
            db.execute(
                "INSERT INTO profiles(id,name,kind,domains,created_at) VALUES(?,?,?,?,?)",
                (pid, "Shared Chrome", "managed", "[]", time.time()),
            )
            if source:
                db.execute("INSERT INTO profile_sources VALUES(?,?)", (pid, source))
            db.execute("INSERT OR REPLACE INTO settings VALUES('default_profile',?)", (pid,))
            if pending:
                db.execute("DELETE FROM bindings")
            self._initialized(db)
            return True

    def snapshot(self, agents: list[dict], connected: set[str]) -> dict:
        with self._db() as db:
            default = self._default(db)
            saved = {r["agent_id"]: r for r in db.execute("SELECT * FROM bindings")}
            bindings = {}
            for agent in agents:
                aid = agent["agent_id"]
                row = saved.get(aid)
                mode = row["mode"] if row else "inherit"
                pid = row["profile_id"] if row else None
                effective = default if mode == "inherit" else pid if mode == "profile" else None
                bindings[aid] = {"mode": mode, "profile_id": pid, "effective_profile_id": effective}
            profiles = []
            for row in db.execute("SELECT * FROM profiles ORDER BY created_at, id"):
                profiles.append(
                    {
                        **self._public(row),
                        "connected": row["id"] in connected,
                        "is_default": row["id"] == default,
                        "agent_ids": [
                            aid
                            for aid, b in bindings.items()
                            if b["effective_profile_id"] == row["id"]
                        ],
                    }
                )
            return {
                "profiles": profiles,
                "default_profile_id": default,
                "bindings": bindings,
                "agents": agents,
            }

    def create(self, name: str, kind: str, domains: list[str]) -> dict:
        name = name.strip()
        domains = normalize_domains(domains)
        if not name or len(name) > 80 or kind not in {"managed", "chrome"}:
            raise ValueError("Choose a profile name and a supported browser type")
        if kind == "chrome" and not domains:
            raise ValueError("Choose at least one allowed website for Chrome")
        pid = uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO profiles(id,name,kind,domains,created_at) VALUES(?,?,?,?,?)",
                (pid, name, kind, json.dumps(domains), time.time()),
            )
            return self._public(self._require(db, pid))

    def update(self, profile_id: str, *, name: str | None, domains: list[str] | None) -> None:
        with self._db() as db:
            row = self._require(db, profile_id)
            new_name = name.strip() if name is not None else row["name"]
            new_domains = (
                normalize_domains(domains) if domains is not None else json.loads(row["domains"])
            )
            if not new_name or len(new_name) > 80:
                raise ValueError("Choose a profile name")
            if row["kind"] == "chrome" and not new_domains:
                raise ValueError("Choose at least one allowed website for Chrome")
            db.execute(
                "UPDATE profiles SET name=?, domains=? WHERE id=?",
                (new_name, json.dumps(new_domains), profile_id),
            )

    def assign(self, agent_id: str, mode: str, profile_id: str | None) -> None:
        checked_id(agent_id)
        if mode not in {"inherit", "own", "profile"}:
            raise ValueError("Invalid browser assignment")
        if mode != "profile" and profile_id is not None:
            raise ValueError("Only explicit profile assignments accept a profile ID")
        with self._db() as db:
            if mode == "profile":
                self._require(db, profile_id or "")
            db.execute(
                "INSERT OR REPLACE INTO bindings VALUES(?,?,?)", (agent_id, mode, profile_id)
            )

    def share(self, profile_id: str, scope: str, agent_ids: list[str], known_ids: set[str]) -> None:
        if scope not in {"all", "selected"} or not set(agent_ids) <= known_ids:
            raise ValueError("Choose existing agents and a supported sharing scope")
        with self._db() as db:
            self._require(db, profile_id)
            self._initialized(db)
            if scope == "all":
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES('default_profile',?)", (profile_id,)
                )
                db.execute("DELETE FROM bindings")
                return
            # Existing agents losing access must choose their next account;
            # only future agents fall back to their unused private profile.
            revoked = "revoked-" + uuid4().hex
            if self._default(db) == profile_id:
                inherited = {
                    r[0] for r in db.execute("SELECT agent_id FROM bindings WHERE mode='inherit'")
                }
                explicit = {
                    r[0] for r in db.execute("SELECT agent_id FROM bindings WHERE mode!='inherit'")
                }
                affected = (known_ids - explicit) | inherited
                db.executemany(
                    "INSERT OR REPLACE INTO bindings VALUES(?,'profile',?)",
                    [(aid, revoked) for aid in affected - set(agent_ids)],
                )
                db.execute("DELETE FROM settings WHERE key='default_profile'")
            db.execute("UPDATE bindings SET profile_id=? WHERE profile_id=?", (revoked, profile_id))
            db.executemany(
                "INSERT OR REPLACE INTO bindings VALUES(?,'profile',?)",
                [(aid, profile_id) for aid in set(agent_ids)],
            )

    def remove(self, profile_id: str) -> None:
        with self._db() as db:
            self._require(db, profile_id)
            self._initialized(db)
            db.execute("DELETE FROM pairing WHERE profile_id=?", (profile_id,))
            db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
            # Keep bindings/default as tombstones: revocation must not silently
            # move a task into the agent's old account. The user chooses next.

    def resolve(self, agent: Any) -> ProfileBinding:
        aid = checked_id(agent.agent_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM bindings WHERE agent_id=?", (aid,)).fetchone()
            mode = row["mode"] if row else "inherit"
            pid = (
                self._default(db)
                if mode == "inherit"
                else row["profile_id"]
                if mode == "profile"
                else None
            )
            if pid:
                profile = self._require(db, pid)
                source = db.execute(
                    "SELECT source_agent_id FROM profile_sources WHERE profile_id=?", (pid,)
                ).fetchone()
                domains = tuple(json.loads(profile["domains"]))
                restrictions = tuple(getattr(agent, "browser_allowed_domains", ()))
                if restrictions:
                    if domains:
                        allowed = normalize_domains([d.removeprefix("*.") for d in restrictions])
                        domains = tuple(
                            sorted(
                                {
                                    a if a.endswith("." + b) else b
                                    for a in domains
                                    for b in allowed
                                    if a == b or a.endswith("." + b) or b.endswith("." + a)
                                }
                            )
                        )
                        if not domains:
                            raise ValueError(
                                "The profile and agent have no allowed websites in common"
                            )
                    else:
                        # Adoption changes storage metadata, not this agent's
                        # already-enforced domain policy or active access key.
                        domains = restrictions
                key = pid
                path = (
                    self.data_dir / "society" / "browser-profiles" / pid
                    if profile["kind"] == "managed"
                    else None
                )
                if source is not None:
                    if profile["kind"] != "managed":
                        raise ValueError("Invalid shared browser storage identity")
                    source_id = checked_id(source["source_agent_id"])
                    path = self._source_folder(source_id)
                    key = f"agent-{source_id}"
                return ProfileBinding(key, profile["kind"], pid, path, domains, profile["name"])
            shared_source = db.execute(
                "SELECT 1 FROM profile_sources WHERE source_agent_id=?", (aid,)
            ).fetchone()
        kind = "attach" if str(getattr(agent, "browser_mode", "own")) == "attach" else "managed"
        private = bool(shared_source) and kind == "managed"
        return ProfileBinding(
            f"private-agent-{aid}" if private else f"agent-{aid}",
            kind,
            None,
            self.data_dir
            / "society"
            / aid
            / ("browser-profile-private" if private else "browser-profile"),
            tuple(getattr(agent, "browser_allowed_domains", ())),
            "Own profile",
        )

    def pair_code(self, profile_id: str) -> str:
        code = secrets.token_urlsafe(24)
        with self._db() as db:
            row = self._require(db, profile_id)
            if row["kind"] != "chrome":
                raise ValueError("Only Chrome profiles need pairing")
            db.execute(
                "DELETE FROM pairing WHERE profile_id=? OR expires<?", (profile_id, time.time())
            )
            db.execute(
                "INSERT INTO pairing VALUES(?,?,?)", (_digest(code), profile_id, time.time() + 300)
            )
        return code

    def pair(self, code: str, installation_id: str) -> dict:
        checked_id(installation_id)
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM pairing WHERE code_hash=? AND expires>?",
                (_digest(code), time.time()),
            ).fetchone()
            if row is None:
                raise ValueError("Pairing code expired or invalid; create a new code in Jarvis")
            pid = row["profile_id"]
            self._require(db, pid)
            db.execute("DELETE FROM pairing WHERE profile_id=?", (pid,))
            db.execute(
                "UPDATE profiles SET token_hash=?,installation_id=? WHERE id=?",
                (_digest(token), installation_id, pid),
            )
        return {"profile_id": pid, "token": token}

    def pairing_profile(self, code: str) -> str:
        """Find the lock identity; consumption still revalidates the code transactionally."""
        with self._db() as db:
            row = db.execute(
                "SELECT profile_id FROM pairing WHERE code_hash=? AND expires>?",
                (_digest(code), time.time()),
            ).fetchone()
            if row is None:
                raise ValueError("Pairing code expired or invalid; create a new code in Jarvis")
            return str(row[0])

    def authenticate(self, profile_id: str, token: str, installation_id: str) -> bool:
        if not token or len(token) > 128 or not _ID.fullmatch(profile_id):
            return False
        with self._db() as db:
            row = db.execute(
                "SELECT token_hash,installation_id FROM profiles WHERE id=? AND kind='chrome'",
                (profile_id,),
            ).fetchone()
            return bool(
                row
                and row[0]
                and secrets.compare_digest(row[0], _digest(token))
                and secrets.compare_digest(row[1], installation_id)
            )
