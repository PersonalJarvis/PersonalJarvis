"""Admin CLI — issue-token / list-tokens / revoke / usage.

Operates directly on the SQLite store (no HTTP), for on-box administration:

    python -m keyproxy.cli issue-token --label alice
    python -m keyproxy.cli list-tokens
    python -m keyproxy.cli revoke <token-id>
    python -m keyproxy.cli usage [--token <id>] [--since <unix>] [--until <unix>]

The DB path comes from ``--db`` or ``KEYPROXY_DB_PATH`` (default
``~/.keyproxy/keyproxy.sqlite``). The plaintext token is printed ONCE by
``issue-token`` and is unrecoverable afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from .store import Store, default_db_path
from .tokens import TokenStore
from .usage import UsageStore


def _resolve_db_path(arg_db: str | None) -> str:
    if arg_db:
        return arg_db
    env = (os.environ.get("KEYPROXY_DB_PATH") or "").strip()
    return env or str(default_db_path())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keyproxy", description="keyproxy admin CLI"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: $KEYPROXY_DB_PATH or ~/.keyproxy/keyproxy.sqlite)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue-token", help="issue a new per-user token")
    issue.add_argument("--label", required=True, help="human label for the token")

    sub.add_parser("list-tokens", help="list all tokens (active + revoked)")

    revoke = sub.add_parser("revoke", help="revoke a token by id")
    revoke.add_argument("token_id", help="the token id to revoke")

    usage = sub.add_parser("usage", help="per-token usage report")
    usage.add_argument("--token", default=None, help="filter by token id")
    usage.add_argument("--since", type=int, default=None, help="unix seconds lower bound")
    usage.add_argument("--until", type=int, default=None, help="unix seconds upper bound")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Windows cp1252 stdout safety for non-ASCII labels.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

    parser = _build_parser()
    args = parser.parse_args(argv)
    as_json = bool(args.json)

    store = Store(_resolve_db_path(args.db))
    try:
        tokens = TokenStore(store)
        usage = UsageStore(store)

        if args.command == "issue-token":
            issued = tokens.issue(args.label)
            if as_json:
                print(json.dumps({
                    "id": issued.id,
                    "label": issued.label,
                    "token": issued.plaintext,
                }))
            else:
                print(f"id:    {issued.id}")
                print(f"label: {issued.label}")
                print(f"token: {issued.plaintext}")
                print("(store this token now — it cannot be shown again)")
            return 0

        if args.command == "list-tokens":
            rows = [
                {
                    "id": r["id"],
                    "label": r["label"],
                    "created_at": r["created_at"],
                    "revoked_at": r["revoked_at"],
                }
                for r in tokens.list()
            ]
            if as_json:
                print(json.dumps(rows))
            elif not rows:
                print("(no tokens)")
            else:
                for r in rows:
                    state = "revoked" if r["revoked_at"] else "active"
                    print(f"{r['id']}  {state:<7}  {r['label']}")
            return 0

        if args.command == "revoke":
            ok = tokens.revoke(args.token_id)
            if as_json:
                print(json.dumps({"revoked": ok, "id": args.token_id}))
            else:
                print("revoked" if ok else "unknown token id")
            return 0 if ok else 1

        if args.command == "usage":
            rows = usage.report(
                token_id=args.token, since=args.since, until=args.until
            )
            if as_json:
                print(json.dumps(rows))
            elif not rows:
                print("(no usage)")
            else:
                for r in rows:
                    print(
                        f"{r['token_id'] or '(untracked)'}  "
                        f"calls={r['calls']}  total_tokens={r['total_tokens']}  "
                        f"est_cost=${r['est_cost']:.6f}"
                    )
            return 0
    finally:
        store.close()

    return 2  # pragma: no cover - argparse enforces a valid subcommand


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
