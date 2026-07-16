---
title: "Public Docs Authoring Standard"
slug: public-docs-authoring-standard
summary: "Write clear, accurate, accessible, and privacy-safe product documentation."
section: "Authoring"
section_order: 90
order: 1
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: developer
tags: [docs, writing, accessibility, privacy]
---

This is the binding writing standard for reader-facing pages in
`docs/product/`. It is inspired by the progressive, task-focused structure of
the [Claude Platform documentation](https://platform.claude.com/docs/en/home),
but it uses Personal Jarvis terminology, behavior, and renderer capabilities.

The reader should understand what a feature does, why it matters, how to use
it, how it connects to the rest of Jarvis, and what to do when it does not
work. Technical accuracy stays intact; unnecessary implementation detail does
not.

## Reader promise

Write for a capable person who uses software but may not write code. A reader
must never need repository history, an architecture document, or an acronym
list to understand the page.

- Lead with the outcome the reader can achieve.
- Explain a term where it first appears, in the same sentence.
- Show the shortest safe path before options and edge cases.
- Describe what the reader sees in the app after each important action.
- Keep provider, operating-system, and machine assumptions out of the main
  path.
- Preserve meaningful technical depth in short, clearly labeled sections.

## Frontmatter contract

Every page must start with the existing Jarvis frontmatter plus the four
navigation fields proposed below. Until all index consumers support the new
fields, they remain additive metadata and must not replace `diataxis`, `tags`,
or `related`.

```yaml
---
title: "Use voice with Jarvis"
slug: use-voice
summary: "Talk to Jarvis, choose a voice, and fix common microphone issues."
section: "Use Jarvis"
section_order: 3
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [voice, microphone]
related: [first-run, choose-a-brain-provider]
---
```

| Field | Rule |
|---|---|
| `summary` | One plain-English sentence, 80-160 characters. State the reader outcome, not an internal mechanism. |
| `section` | Stable, human-readable navigation group. Reuse an existing section before creating one. |
| `section_order` | Positive integer that orders navigation groups. Pages in the same section use the same value. |
| `order` | Positive integer that orders pages within a section. Do not use file names to imply order. |

Keep titles under 50 characters when possible. Slugs are lowercase kebab-case
and must not change after publication. Use `owner: maintainers`; never put a
person's name in public frontmatter. Update `last_reviewed` after checking the
page against the current product.

## Required page anatomy

The app renders the frontmatter title as the page heading, so do not add a
second H1 in the body. Use this sequence unless the page type makes a section
irrelevant:

1. **Opening:** one or two short paragraphs that say what the feature does,
   who it helps, and the result the reader can expect.
2. **Before You Start:** only real prerequisites, permissions, or costs. Omit
   the section when there are none.
3. **Use the Feature:** numbered, outcome-oriented steps. Begin each step with
   an action and end with the visible result.
4. **How It Fits Together:** the required relationship section described
   below.
5. **Check That It Works:** one fast, observable verification path.
6. **Troubleshooting:** the most likely symptoms and safe fixes.
7. **Next Steps:** two to four relevant internal links with a reason to follow
   each one.

Reference and explanation pages may replace **Use the Feature** with a more
accurate H2, such as **Choose an Option** or **How It Works**. They still need
**How It Fits Together**, **Check That It Works**, **Troubleshooting**, and
**Next Steps**.

## How It Fits Together

Every product page must contain an H2 named exactly `How It Fits Together`.
Explain relationships from the reader's point of view, not as a module list.

Cover the relevant parts of this flow:

1. What starts the feature: a click, spoken request, scheduled action, or
   incoming message.
2. What Jarvis does next in plain language.
3. Which adjacent feature supplies input or receives the result.
4. Where permissions, safety confirmation, or a connected service applies.
5. What happens when a preferred provider or local capability is unavailable.
6. Which linked page explains the adjacent feature in more detail.

Use a short numbered flow, a small table, or a labeled image. Do not present
internal class names as the explanation. If a technical name is necessary,
state the everyday meaning first and put the identifier in parentheses or
inline code.

## Plain-English rules

- Write in English, second person, active voice, and present tense.
- Prefer familiar verbs: **choose**, **open**, **connect**, **review**, and
  **try again**.
- Use one idea per paragraph. Keep paragraphs to one to three sentences and
  normally below 60 words.
- Keep H2 sections focused. Split a section that exceeds roughly 350 words.
- Use H2 and H3 headings only. Avoid H4-H6 and headings that contain only one
  sentence of content.
- Define jargon on first use: "a provider - the service that answers your
  request." Do not send readers to a glossary for the first explanation.
- Expand acronyms on first use. Avoid acronyms that appear only once.
- Use `Jarvis-Agents` and `Jarvis-Agent`; do not revive historical internal
  names.
- Address limitations directly. Do not use marketing claims such as
  "seamless," "magic," or "always works."
- Distinguish what Jarvis does automatically from what the reader must do.
- Keep implementation history, phase names, test architecture, and design
  debates out of product pages unless they change the reader's decision.

## Steps, examples, and code

Use numbered lists for actions in sequence and bullets for choices or facts.
Do not hide a required action in prose.

Most pages should contain zero to three code blocks. A code block must:

- solve one immediate task;
- be preceded by one sentence that explains why to run it;
- be followed by the expected result or the next action;
- include a language label such as `powershell`, `bash`, `json`, or `text`;
- stay below 20 lines when possible and never exceed 25 lines;
- exclude unrelated setup, generated output, stack traces, and repeated
  alternatives.

Prefer the app or `jarvis` CLI for user actions. Do not teach readers to edit
`jarvis.toml`, export credentials, or call private REST routes when the product
provides an in-app path. Put long schemas, complete command catalogs, and
implementation samples in a linked reference page instead of a task page.

## Callouts

The renderer supports these GitHub-style callouts:

```markdown
> [!tip] Use this for a helpful shortcut.

> [!info] Use this for context that changes a choice.

> [!warning] Use this before an action that can cost money, expose data, or
> require confirmation.

> [!note] Use this for a small but relevant detail.
```

Use no more than two callouts in a normal page. Keep each to one idea and no
more than one short paragraph. A required step or safety instruction must also
appear in the main flow; readers must not have to infer importance from color.

## Tables and visuals

Use a table only when the reader is comparing options, states, or exact
mappings. Keep it to five columns and about ten rows; split larger material
into a reference page. Every column needs a descriptive header, and each cell
should be a phrase rather than a paragraph.

Prefer a short numbered flow over a decorative diagram. When an image
materially improves understanding:

- store it beside the page in a clearly named asset folder;
- use the desktop asset route
  `/api/docs/asset/<page-slug>/<relative-file>`;
- write alt text that communicates the same purpose as the image;
- crop out unrelated windows, notifications, account details, and values;
- keep labels readable at the app's normal content width;
- explain the important takeaway in nearby text.

Do not use raw HTML, MDX components, Mermaid, embedded scripts, or
color-dependent diagrams. The current ReactMarkdown renderer supports
GitHub-Flavored Markdown, heading anchors, links, tables, fenced code, images,
and the callouts listed above.

## Verification and troubleshooting

`Check That It Works` must tell the reader what success looks like in the
product. Use one representative action and one observable result. Do not use
an internal test suite as the public verification method.

`Troubleshooting` should cover two to five likely problems. Use this pattern:

| What you see | What it usually means | What to do |
|---|---|---|
| A clear, reader-visible symptom | A plain-language cause | The safest first fix, then a link for deeper help |

Never promise that a restart, reinstall, or credential replacement will fix
every case. Say when the reader should check permissions, service status, a
different provider family, or the dedicated troubleshooting page. Error text
must match the current product when quoted.

`Next Steps` must link to two to four pages that continue the reader's journey.
Do not write a bare link list. Add a short reason, for example: "Read
[Computer Use](computer-use) to let Jarvis work with desktop apps."

## Accessibility

- Use descriptive link text; never "click here" or a raw URL as the label.
- Give every meaningful image useful alt text. Use empty alt text only for a
  truly decorative image.
- Do not encode meaning only through color, position, or an icon.
- Keep headings in order: H2, then H3. Do not skip levels.
- Write complete instructions for keyboard and screen-reader users; do not
  assume mouse input.
- Describe buttons and fields by their visible labels, with the view name when
  needed.
- Avoid directional instructions such as "the box on the right" unless a
  stable label is also included.
- Keep table headers concise and make lists grammatically parallel.

## Privacy and credential safety

Public documentation is treated as publishable source. It must contain none of
the following, even in screenshots, logs, sample output, comments, metadata,
or frontmatter:

- real or secret-shaped API keys, tokens, passwords, cookies, authorization
  headers, private keys, or recovery codes;
- personal names, email addresses, phone numbers, account names, internal
  project identifiers, machine identifiers, Windows security identifiers, or
  hostnames;
- personal paths such as `C:\Users\<person>\...`, local vault contents,
  conversation text, contact data, or private repository URLs.

Do not invent key-shaped sample values. When a credential is required, say:
"Open **Settings > API Keys**, choose your provider, and enter the credential
in the app." Never instruct readers to paste a secret into chat, voice input,
`jarvis.toml`, a command, or a screenshot.

Use neutral paths such as `<project-folder>` only when a path is necessary.
Use reserved example domains such as `example.com` and clearly fictional,
non-secret identifiers. Before publishing, inspect rendered images and copied
command output as carefully as the Markdown source.

## Accuracy and review checklist

An author must verify the current UI, implementation, and supported command
before describing them. Do not infer behavior from an old plan or a function
name.

- [ ] The opening states a concrete reader outcome.
- [ ] First-use jargon is explained without reducing technical accuracy.
- [ ] Steps match current labels and produce the stated visible result.
- [ ] `How It Fits Together` explains adjacent features and failure behavior.
- [ ] `Check That It Works`, `Troubleshooting`, and `Next Steps` are present.
- [ ] Internal links resolve by stable slug; external links are necessary and
      authoritative.
- [ ] Code blocks are short, runnable where applicable, and free of secrets.
- [ ] The page does not assume one provider, one operating system, or one
      maintainer machine.
- [ ] Images, tables, callouts, and headings remain understandable without
      color or mouse input.
- [ ] Source and rendered output contain no secrets or personal information.
- [ ] Frontmatter is complete, ordering values do not collide, and
      `last_reviewed` is current.

If any item cannot be verified, keep the page in `draft` status and state the
known limitation plainly. Never fill a gap with a plausible but unconfirmed
instruction.
