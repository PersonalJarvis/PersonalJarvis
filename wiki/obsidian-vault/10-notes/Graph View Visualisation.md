---
tags: [tooling, visualisation, feedback-loop]
aliases: [Graph View, Knowledge Graph]
created: 2026-05-12
---

# Graph View Visualisation

A force-directed diagram of your notes, drawn live from the Wikilinks between them. Obsidian renders it in real time; Roam and Logseq have their own variants. The visual exists less for navigation and more as a *diagnostic*.

## What the Graph Tells You

- **Dense clusters** = topics you have already thought through.
- **Bridges between clusters** = the unexpected connections that produce new ideas.
- **Orphan nodes** = notes that were captured but never integrated. They are the working list of what to think about next.
- **Hubs** (high-degree nodes) = your core concepts. Their stability is a sign your model is solidifying.

## Why It Matters for PKM

Without the graph, [[Personal Knowledge Management]] feels like writing into a void. The visualisation provides the feedback loop that makes the practice self-reinforcing — see also the maturity argument in [[Evergreen Notes]].

## Technical Substrate

Every modern PKM tool builds its graph on top of two layers: [[Markdown as Foundation]] for storage, and a Wikilink parser for edges. The original idea — that ideas surface through neighbours — predates the tooling and traces back to the [[Zettelkasten Method]].

## Anti-patterns

- **Optimising for the graph instead of for thought.** A pretty graph with shallow notes is a vanity metric.
- **Adding "MOC" (Map of Content) hub-notes too early.** They inflate the visualisation without earning their place.

## See Also

- [[Personal Knowledge Management]] — the context
- [[Zettelkasten Method]] — the origin
- [[Evergreen Notes]] — the discipline
- [[Markdown as Foundation]] — the substrate
