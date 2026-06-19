# Phase B1 Instance C — Handoff (AtomicWriter + BackupManager)

**Branch:** `impl/wiki-memory-b1-writer` (based on `impl/wiki-memory` @ `ef041608`)
**Owner:** Instance C — Schreib-Sicherheit
**Status:** ready for Wave-2 integration

---

## What landed

Two production modules and two unit-test files. Plus a copy of
`protocols.py` (see "Open questions" below).

```
jarvis/memory/wiki/atomic_writer.py     ~340 LOC  five-step write pipeline
jarvis/memory/wiki/backup.py            ~250 LOC  tar.gz snapshot + restore + rotation
jarvis/memory/wiki/protocols.py         (carbon-copy of Instance A's contract)

tests/unit/memory/wiki/test_atomic_writer.py   17 tests
tests/unit/memory/wiki/test_backup.py          16 tests
tests/unit/memory/wiki/conftest.py             FakePageRepository + vault_root fixture
tests/unit/memory/wiki/__init__.py             package marker
```

`pytest tests/unit/memory/wiki/ -v` → **33 passed in 1.55 s**.

## What `AtomicWriter` does (and what it deliberately does not)

The class implements the binding `AtomicWriter` Protocol from
`jarvis/memory/wiki/protocols.py`. One `apply(updates, *, repo)` call
runs the five-step pipeline documented in
`docs/phase-b1-wiki-curator/README.md` Part 5 → Instance C:

1. **30-second concurrent-edit lock.** Each update's target (and, for
   renames, the rename source) is `stat()`-ed. If `now - mtime < 30 s`,
   the update is skipped and the path is reported under
   `WriteResult.skipped_due_to_recent_edit`. No exception — Obsidian
   races are the explicit motivating case.
2. **Exactly one backup per `apply()`.** A tar.gz of the whole vault
   (minus `_archive/` and `attachments/`) is written to
   `data/backups/wiki-<YYYYMMDDHHMMSS>.tar.gz`. Skipped entirely when
   no update survives step 1.
3. **Same-drive tempfile + `os.replace`.** Tempfile lives inside the
   target's parent directory so the swap is atomic on Windows. A
   defensive `splitdrive` assertion raises `AtomicWriteError` if a
   caller ever produces an update whose target is on a different drive
   from the vault root.
4. **Per-page validation via `repo.parse`.** The just-written file is
   re-read from disk and fed back through the `PageRepository`. Pages
   that come back `is_schema_valid=False` (or that raise inside
   `parse`) are rolled back individually from the snapshot. Brand-new
   pages with no archive member are deleted instead. Other pages stay
   applied — partial success is the expected mode.
5. **Backup rotation as hygiene.** `BackupManager.rotate()` deletes
   archives beyond `max_backups` (default 10). Failures here log but
   never raise — a successful mutation is not invalidated by GC.

Things `AtomicWriter` **does not** do:

* It does not call the LLM. It receives fully rendered `new_body`
  strings from Instance D and trusts them.
* It does not touch `log.md` or `index.md`. Those are Instance B's
  surface.
* It does not import from `vault_index.py` or `curator_llm.py`. Its
  only Instance dependency is the `PageRepository` Protocol from
  Instance A.

## Operations supported

| `PageUpdate.operation` | Behaviour |
|---|---|
| `create` | Write `new_body` to a brand-new target. Validation failure → file is deleted. |
| `update` | Write `new_body` over an existing target. Validation failure → restored from snapshot. |
| `rename` | Write `new_body` to the new path, then `unlink` the old `rename_from`. Both paths are checked against the 30-s lock. |
| `archive` | Move the existing target into `_archive/<vault-relative-path>`. `new_body` is ignored. No-op when the target does not exist. |

Unknown operation strings raise `AtomicWriteError` up front — this is
treated as a programmer bug, not a runtime data issue.

## Public API surface

```python
from jarvis.memory.wiki.atomic_writer import AtomicWriter, AtomicWriteError
from jarvis.memory.wiki.backup import BackupManager, BackupError

writer = AtomicWriter(
    vault_root=vault_root,
    backup_dir=vault_root.parent / "backups",
    max_backups=10,
)
result = await writer.apply(updates, repo=page_repo)
# result.applied / .skipped_due_to_recent_edit / .failed_validation / .backup_path
```

`BackupManager` exposes `snapshot()`, `restore(backup_path, relpath)`,
`list_backups()`, `rotate()`. It is reusable on its own if Wave 2 wants
a "snapshot before manual edit"-style command later.

## Test coverage (33 tests)

`test_atomic_writer.py` (17):

* Step 1 (lock): recent file is skipped, 5-minute-old file passes, new
  page is never lock-skipped.
* Step 2 (backup): exactly one archive per `apply()` even with three
  page updates.
* Step 4 (validation rollback): invalid neighbour is restored from
  snapshot while the valid one stays applied; brand-new invalid page
  is deleted entirely.
* Step 3 mid-write crash: `os.replace` raised on page #2 of three →
  pages #1 and #3 still applied, page #2 unchanged, no `.tmp`
  leftovers.
* Step 5 rotation: 11 calls with `max_backups=10` → exactly 10
  archives remain.
* Operation routing: `rename` writes new path and unlinks the source;
  `archive` moves into `_archive/...`; archive of a missing page is a
  silent no-op.
* Defensive: target outside vault → `AtomicWriteError`; unknown
  operation → `AtomicWriteError`; rename without `rename_from` →
  `AtomicWriteError`; empty input → empty `WriteResult` with empty
  `backup_path`.

`test_backup.py` (16):

* Snapshot creates `wiki-<ts>.tar.gz`, excludes `_archive/` and
  `attachments/`, uses vault-relative arc names, disambiguates within
  one second, raises on missing vault root.
* Restore round-trips a modified page, creates missing subdirs,
  refuses path traversal (`../etc/passwd`), rejects missing members,
  rejects missing archives.
* Rotation keeps the newest N, returns the deleted paths, is a no-op
  under cap; default cap is 10; constructor rejects `max_backups=0`.
* Glob pattern matches what `snapshot()` produces (drift guard).

## Open questions / deviations from the briefing

* **`protocols.py` carbon-copy.** The base commit `ef041608` does not
  contain `protocols.py`. Instance A's branch has not yet merged into
  `impl/wiki-memory` at the time of this hand-off, so I committed a
  verbatim copy of the agreed contract on my branch so that imports
  and tests compile in isolation. Wave 2 must resolve any drift by
  keeping Instance A's authoritative version. The copy here matches
  the Protocols spelled out in `README.md` Part 4 exactly.
* **Same-drive assertion.** I added a belt-and-suspenders
  `os.path.splitdrive` check on top of the relative-to-vault
  assertion. If a future caller ever produces a `PageUpdate.target_path`
  off-drive, the writer fails fast with a clear message instead of
  silently degrading to a non-atomic rename.
* **`async apply` via `asyncio.to_thread`.** The pipeline is
  synchronous file I/O with one synchronous `PageRepository.parse`
  call inside the validation step. Per the briefing's "never block the
  asyncio loop" rule, the whole pipeline runs on a worker thread. The
  parse call is itself async so I use `asyncio.run(parse_call)` inside
  the worker — safe because we hold no loop reference in that thread.
  If Wave 2 wires a `PageRepository` that depends on an outer event
  loop, that contract has to change.
* **Archive operation `applied` semantics.** Even when the archive
  target does not exist (silent no-op), the writer still lists the
  update under `WriteResult.applied`. The contract reads "applied =
  pages that were successfully written"; archiving a missing page is
  a successful operation that produced no write. If Wave 2 prefers
  the path to land under a new `WriteResult.no_op` bucket, I am happy
  to add it — but I have not introduced a new field unilaterally.
* **No `log.md` write.** Per the briefing, log entries are Instance B's
  responsibility. Wave 2's `WikiCurator` will call
  `vault.append_log_entry(...)` after `writer.apply(...)` returns.

## Things I deliberately did **not** touch

* Anything under `jarvis/memory/wiki/templates/`.
* `jarvis/memory/wiki/__init__.py` (left as Phase B0 wrote it).
* Any production code outside `jarvis/memory/wiki/`.
* `data/workspace/` (the runtime vault — tests use `tmp_path`).
* The existing `tests/unit/memory/conftest.py` for the legacy curator.

## How to verify

```powershell
git checkout impl/wiki-memory-b1-writer
pytest tests/unit/memory/wiki/test_atomic_writer.py tests/unit/memory/wiki/test_backup.py -v
# 33 passed
```

No `print()` calls in production code. No German strings or
identifiers. No hardcoded brain provider or model (Instance C does not
talk to any provider). All public classes/functions have at least one
test.
