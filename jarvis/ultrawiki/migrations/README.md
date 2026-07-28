# UltraWiki migrations

Forward-only schema migrations for the UltraWiki store (`data/ultrawiki.db`),
tracked via SQLite's `PRAGMA user_version` — the same engine contract as
`jarvis/memory/migration_runner.py`, which `store.py` invokes with this
directory.

The contract:

- `jarvis/ultrawiki/schema.sql` is the idempotent BASE schema
  (`CREATE ... IF NOT EXISTS`), applied on every open. It can never alter a
  table that already exists (for example widening a `CHECK` constraint).
- Any change to an existing table ships as an `NNNN_<slug>.sql` file in this
  directory, numbered `0001`, `0002`, ... strictly ascending. The runner
  applies every file whose number exceeds the database's current
  `user_version`, then bumps the pragma to the highest applied number —
  re-running is a no-op.
- Migrations are FORWARD-ONLY. Never edit or delete a shipped migration file;
  ship a new numbered file instead. Thousands of independent installs sit on
  different schema versions, and a rewritten historical file silently diverges
  them.
- Each migration file wraps its own statements in `BEGIN` / `COMMIT`. A
  failure leaves `user_version` at the previous head, so the next open retries
  from the same point.
- Files that do not match `NNNN_<slug>.sql` are ignored (this README included).

Shipped so far: `0001` (multi-chunk documents), `0002` (the shadow embedding
space — widening the `uw_embeddings` key so a model switch can build the new
vector space alongside the live one), `0003` (the identity layer: entities,
identifiers, the confirmation queue and the reversible merge log), `0004`
(episodic events: the bi-temporal `uw_events` table, its participant links and
its own FTS5 keyword index). The Postgres backend applies its DDL idempotently
from `PostgresStore.ddl_statements()`, including the in-place key widening that
mirrors `0002`, the identity tables that mirror `0003` and the event tables
that mirror `0004`; it receives its own version tracking when a change ships
that idempotent DDL cannot express.

A migration that only ADDS tables (like `0003` and `0004`) is still the right home for
them: keeping one dialect-complete DDL per feature in one numbered file beats
splitting it between here and `schema.sql`, and the runner applies pending
files to fresh databases too (a new store starts at `user_version = 0`).
