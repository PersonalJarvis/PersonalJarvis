# UltraWiki connector test fixtures

Tiny, committed source trees the connector tests walk read-only:

- `obsidian_vault/` — a minimal Obsidian vault: two visible notes plus a
  `.obsidian/` config dir and a `.trash/` note that connectors must skip.
- `local_folder/` — a mixed folder: markdown + plain text (yielded by the
  default extension filter) and a `.bin` file (excluded by default).
- `wiki_vault/` — a minimal built-in-wiki vault with one page under
  `entities/` and one under `concepts/`.

Tests that need controlled modification times (cursor round-trips) build
their trees in `tmp_path` instead; these committed trees only pin the
walk, skip, id, title, and permalink behavior.
