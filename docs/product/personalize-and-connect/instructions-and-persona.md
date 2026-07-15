---
title: "Instructions and Persona"
slug: instructions-and-persona
summary: Shape how Jarvis responds without changing providers, memories, or safety rules.
section: "Personalize and connect"
section_order: 3
order: 5
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [instructions, persona, personalization, profile, safety]
related: [chats, profile-and-contacts, safety-and-approvals]
---

You can shape how Jarvis communicates without changing the service or model
that answers you. For most people, **standing instructions** are the right
place to set tone, formatting, language preferences, and sensible defaults.

The **System Prompt** is an advanced editor for the persona, which is the
general character and speaking style Jarvis starts with. It is broader than
standing instructions, but it still cannot add tools, grant permissions, or
remove safety checks.

## Choose the Right Layer

| Layer | Use it for | Where to change it | What it does not change |
|---|---|---|---|
| **Standing instructions** | Your preferred tone, answer shape, defaults, and working habits | The sidebar page named after your assistant, ending in `.md` | Providers, stored knowledge, permissions, or safety rules |
| **Packaged persona** | The built-in voice, warmth, and response style | Used automatically until you save a custom System Prompt | Facts about you or access to tools |
| **Custom System Prompt** | Replacing the packaged persona with your own broader behavior guide | **Settings > System Prompt** | Runtime capabilities, approvals, or enforced language settings |
| **Profile and Contacts** | Your identity and preferences, plus the people you choose to save | **Profile** and **Contacts** | General assistant behavior or project knowledge |
| **Wiki and Memory** | Facts, notes, projects, and context Jarvis can recall | **Wiki** and memory features | Tone rules or provider selection |
| **Provider and model** | The service and model that produce an answer | **API Keys & Providers** | Your saved instructions, profile, or memory |

> [!warning] Anything you save in an instruction or persona editor can become
> part of the context given to the model that answers a turn. Do not put API
> keys, passwords, private records, or other secrets in either editor.

## Before You Start

Write down one or two outcomes you want. Start with a small instruction set so
you can tell which change caused the result. If you mainly want Jarvis to know
your name, form of address, or communication preferences, update your
[Profile and Contacts](profile-and-contacts) instead.

Prefer standing instructions unless you have a clear reason to replace the
whole persona. They are easier to change and preserve the tested default
persona underneath.

## Add Standing Instructions

1. Open the sidebar and choose the page whose label ends in `.md`. The label
   uses your assistant's current name and the page shows an **Active** or
   **Empty** badge.
2. Select **Start from template** if you want headings for communication style,
   language, preferred actions, things to avoid, and standing facts. This only
   fills the editor; it does not save anything yet.
3. Replace the prompts with short, direct preferences. For example, ask for
   plain language, short paragraphs, or a recommendation when several safe
   options are available.
4. Select **Save instructions**. A confirmation appears, and the change applies
   to the assistant's next message without a restart.
5. Open [Chats](chats) and send a request that makes the preference easy to
   observe.

Use **Revert changes** to discard edits that you have not saved. To clear all
standing instructions, empty the editor and select **Save instructions**. The
page returns to its empty state.

## Change the Persona

A system prompt is the high-level guidance a model receives before your
message. Changing it can affect tone, length, spoken output, and general
behavior across many conversations, so treat this as an advanced setting.

1. Open **Settings** and find **System Prompt**. The editor shows the effective
   persona and a **Default** or **Custom** badge.
2. Review the whole text before changing it. Saving an edit creates a custom
   persona that replaces the packaged persona layer; it is not a small patch on
   top of the default.
3. Keep the persona focused on behavior that a model can follow, such as tone,
   response structure, and how spoken answers should sound.
4. Select **Save prompt**. The badge changes to **Custom**, and the new persona
   applies on the next message without a restart.
5. Select **Reset to default** if you want to remove the custom persona and
   restore the packaged version.

The editor does not allow an empty custom prompt. Use **Reset to default**
instead of deleting all text.

## Write Instructions That Work Well

| Prefer | Avoid |
|---|---|
| One clear rule per bullet | Long, conflicting paragraphs |
| Observable requests such as “Use short paragraphs” | Vague requests such as “Be better” |
| A priority when two preferences may conflict | Demands to bypass approvals or permissions |
| Stable preferences you want on future turns | One-time requests that belong in the current chat |

Keep current tasks in the conversation. Keep durable facts about yourself in
the Profile, and keep notes or project knowledge in Wiki and Memory. This makes
each source easier to review, correct, or remove later.

## How It Fits Together

One answer is assembled from several independent layers:

1. You send a message in **Chats** or start a voice turn.
2. Jarvis combines the active persona, your standing instructions, relevant
   profile facts, available memory, and the capabilities present for that turn.
3. The selected provider and model produce an answer from that context. A
   different model may interpret complex wording differently, but switching
   providers does not delete the saved layers.
4. If the answer requires an action, permissions and the safety tier decide
   whether it can run, needs approval, or must be blocked. Text in either editor
   cannot change that decision.
5. The result returns to the chat or voice conversation. Relevant new facts can
   be saved through Profile or Memory features; they do not automatically
   become new behavior rules.

Standing instructions can refine the default persona when the two differ on
style. Dedicated runtime settings, such as an explicit reply-language choice,
and non-overridable tool and safety rules still take priority. Read
[Providers and API Keys](providers-and-api-keys) for the service and model
layer, and [Wiki and Memory](wiki-and-memory) for stored knowledge.

## Check That It Works

Save one distinctive but harmless standing preference, such as asking for a
one-sentence summary at the start of longer answers. Send a new multi-part
question in **Chats**. The next answer should show that structure.

Then remove the test instruction and save again. A later answer should no
longer be expected to follow it. This checks both applying and clearing without
changing your provider, profile, or memory.

## Current Limitations

- Instructions are guidance to a model, not a deterministic formatting engine.
  Very long, ambiguous, or conflicting rules may be followed inconsistently.
- Models can respond differently to the same instruction. Test important
  behavior again after changing the active model.
- Replacing the System Prompt removes the packaged persona layer until you
  reset it. Structural capability, language, permission, and safety controls
  remain separate.
- An instruction applies from the next assistant message. It does not rewrite
  earlier chat messages or stored memories.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Save instructions** is unavailable | The editor has not changed, or the page is still loading | Make one small edit, then try again |
| Your preference is ignored | The rule is vague, conflicts with another rule, or the model handled it differently | Reduce it to one observable instruction and test a new message |
| Old behavior remains after clearing | The behavior may come from the persona, Profile, Memory, or the current chat context | Start a new test message and review each separate layer |
| The System Prompt cannot be saved | The editor is empty or the save request failed | Restore some text, or use **Reset to default** |
| Jarvis still asks for approval | The requested action is controlled by safety and permissions | Review [Safety and Approvals](safety-and-approvals); do not add bypass instructions |

If both editors show an error or fail to load, keep your text in a temporary
local note, reopen the view, and follow [Troubleshooting](troubleshooting)
before trying to save again.

## Next Steps

- Read [Chats](chats) to test a preference in a fresh conversation.
- Review [Profile and Contacts](profile-and-contacts) to store facts about you
  and the people you know in the right place.
- Use [Wiki and Memory](wiki-and-memory) for project knowledge and facts you
  want Jarvis to recall.
- Read [Safety and Approvals](safety-and-approvals) to understand which action
  rules cannot be changed by instructions or a persona.
