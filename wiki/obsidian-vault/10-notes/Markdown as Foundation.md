---
tags: [format, foundational, tooling]
aliases: [Markdown, CommonMark]
created: 2026-05-12
---

# Markdown as Foundation

Markdown is the lowest-friction format that survives a tool migration. A vault written in Markdown today opens in any editor in 2040, which is exactly the longevity a knowledge base needs.

## Why It Won

- **Human-readable in raw form** — even without a renderer, the structure is obvious.
- **Plain-text durability** — no proprietary binary blob between you and your text.
- **Tool-agnostic** — Obsidian, Bear, iA Writer, and `vim` all read the same files.
- **Extensible without breaking** — frontmatter, callouts, and Wikilinks compose with the base spec.

## Wikilinks as the Killer Extension

The CommonMark spec does not define `[[…]]` syntax. Obsidian and Roam adopted it as a deliberate convention to make linking cheaper than the standard `[label](url)` form. This single ergonomic shift is what makes the [[Zettelkasten Method]] practical at scale, and is what powers the [[Graph View Visualisation]].

## Trade-offs

Markdown's flexibility is also its trap — every tool dialect (GFM, MDX, Pandoc) extends the spec slightly differently. Treat it like a contract: stick to the [[Evergreen Notes]] subset that all your tools agree on, and your vault outlives any single editor.

## See Also

- [[Zettelkasten Method]] — what cheap linking enables
- [[Evergreen Notes]] — the discipline that runs on this substrate
- [[Personal Knowledge Management]] — the broader context
- [[Graph View Visualisation]] — what cheap linking renders into
