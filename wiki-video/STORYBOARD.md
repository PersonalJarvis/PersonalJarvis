# Wiki Tutorial Video — Storyboard & QA Gate

**Format:** 1920×1080 (16:9), 30 fps, 1800 frames = **60.0 s**. Composition id `WikiVideo`.
**Subject:** the Personal Jarvis "Wiki" — the LLM-maintained long-term memory vault.
**Brand:** near-black charcoal `#0e0d0c`, single gold accent `#e7c46e`, Gigi ghost logo,
Inter + JetBrains Mono. Content is sourced from the real code (see references per scene).

## Scenes (frame budget sums to 1800)

1. **Intro — 0–240f (8.0 s).** Gigi ghost logo fades/springs in on charcoal; title
   "The Jarvis Wiki", subtitle "How your assistant remembers." One gold hairline.
2. **The idea — 240–570f (11.0 s).** Karpathy's LLM-Wiki. "The assistant is the *editor*,
   the Markdown files are the codebase, Obsidian is the IDE." Compiled once & maintained —
   not re-derived on every query. *(schema.md:13-17; ADR-0013)*
3. **Architecture — 570–930f (12.0 s).** Three-tier memory (short → mid → long, one-way flow)
   and the two-stage sleep-time curator: Conversation → Stage 1 Extractor (cheap, ADD-only) →
   SQLite journal → Stage 2 Consolidator (ADD / UPDATE / NOOP / INVALIDATE) → atomic write →
   vault. Runs off the voice path. *(design spec §4; extractor.py; consolidator.py)*
4. **A page — 930–1230f (10.0 s).** A real Markdown page card: YAML frontmatter + sections +
   `[[wikilinks]]` + backlinks. Entity / Concept / Project / Person types. Local, Git-diffable,
   portable. *(schema.md:36-202; page.py)*
5. **Read-back & why — 1230–1560f (11.0 s).** FTS5 keyword search (never vectors) → injected
   "## Wiki context" before each turn + the wiki-recall tool. Engineered: cheap model by
   default, loud-vs-silent failure contract, key-aware self-healing provider chain.
   *(search.py:225-226; wiki_context.py; provider_chain.py)*
6. **Outro — 1560–1800f (8.0 s).** Gigi ghost returns; tagline "Local. Private. Portable.
   Self-healing."; wordmark "Personal Jarvis".

## Visual QA checklist (must pass twice in a row)

- [ ] Real Gigi ghost logo visible & undistorted (intro + outro)
- [ ] Brand colors held: near-black BG, exactly one gold accent, no foreign look
- [ ] Text readable, nothing cut off, no overflow, safe margins respected
- [ ] Timing 55–65 s, no empty/hanging frames, clean transitions
- [ ] 16:9 / 1920×1080 correct, nothing squished or distorted
- [ ] Content correct (mirrors the real wiki system)
