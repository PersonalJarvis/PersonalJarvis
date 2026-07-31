---
schema_version: "1"
name: plugin-canva
description: Create, search and export the user's Canva designs.
when_to_use: Use when the user mentions Canva, or asks for a design, poster, presentation or graphic.
category: creativity
plugin_id: canva
intent_verbs: [erstell, entwirf, zeig, such, exportier, create, design, show, find, export, crea, diseña, muestra, busca, exporta]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [canva, canva-design, canva design, canva-vorlage, canva template, canva-präsentation, canva presentation, canva poster, canva grafik]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(canva)"  # i18n-allow: spoken-input vocabulary
requires_tools: [canva]
risk_policy:
  default_tier: monitor
---

Use the canva/* tools to create, search and export the user's designs.
- Ask what the design is for only when the request is genuinely ambiguous; otherwise pick a sensible format and say which one you chose.
- Search existing designs before creating a new one when the user refers to something they already made.
- Report the design title and hand back the link.
- Resizing needs Canva Pro, and brand kits and templates need Enterprise. If a call fails for that reason, say so plainly rather than retrying.
