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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [instructions, persona, personalization, profile, safety]
related: [chats, profile-and-contacts, safety-and-approvals]
---

You can change how Jarvis speaks and works with you without changing the
provider or model. Use standing instructions for personal preferences such as
tone, answer format, and default choices.

The **System Prompt** editor controls the broader persona, including its voice
and general response style. It replaces the editable persona layer, not every
runtime instruction. It cannot add tools, grant access, change the selected
reply language, or remove safety checks.

## Choose the Right Layer

| Layer | Use it for | Where to change it | What it does not change |
|---|---|---|---|
| **Standing instructions** | Free-form preferences for tone, answer shape, forms of address, and default choices | The sidebar page named `<assistant name>.md` | Providers, stored knowledge, tool access, or safety rules |
| **Packaged persona** | The built-in voice, warmth, and general response style | Used automatically while the System Prompt badge says **Default** | Facts about you, available tools, or permissions |
| **Custom System Prompt** | Replacing the packaged persona with your own broad behavior guide | **Settings > System Prompt** | Runtime identity, tool availability, reply-language controls, approvals, or safety rules |
| **Profile** | Structured facts about you, including your name, preferred form of address, and communication preferences | **Profile** | A complete persona or project knowledge |
| **Wiki and Memory** | Notes, projects, and facts Jarvis may use as relevant context | **Wiki** and memory features | Standing behavior rules or provider selection |
| **Provider and model** | The service and model that produce a response | **API Keys & Providers** | Your locally saved persona, instructions, Profile, or Memory |

The `<assistant name>.md` label follows the name in your wake word. If no name
is available, the page is named `Assistant.md`.

> [!warning] Jarvis sends the active persona and standing instructions to the
> model as request context. A remote provider receives that text. Do not save
> API keys, passwords, private records, or other secrets in either editor.

## Before You Start

Choose the setting that owns the result you want:

- For a fixed response language, use **Settings > Languages > Reply Language**.
- For your name, preferred form of address, or structured communication
  preferences, use **Profile**.
- For a free-form preference such as short paragraphs or a default answer
  format, use standing instructions.
- Replace the System Prompt only when you want to change the broader persona.

Start with one or two clear preferences. A small set is easier to test and less
likely to contain conflicting rules.

## Add Standing Instructions

1. Open the sidebar and choose the page ending in `.md`. Its heading shows the
   same filename and an **Active** or **Empty** badge.
2. Select **Start from template** if you want headings for communication style,
   language and locale, actions to take or avoid, and facts about you. This
   fills the editor but does not save it.
3. Replace the prompts with short, direct preferences. For example, ask for
   plain language, short paragraphs, or a recommendation when several safe
   choices are available.
4. Select **Save instructions**. The **Active** badge appears, and Jarvis reads
   the saved text for the next response without a restart.
5. Open [Chats](chats) and send a request that makes the preference easy to
   observe.

Select **Revert changes** to discard edits you have not saved. To clear all
standing instructions, empty the editor and select **Save instructions**. The
badge changes to **Empty**, and the saved instructions file is removed.

## Change the Persona

A system prompt is high-level guidance sent to a model before your message.
The editor exposes Jarvis's editable persona layer. Runtime rules are added
separately and remain in force.

1. Open **Settings** and find **System Prompt**. The editor shows the persona
   currently in use and a **Default** or **Custom** badge.
2. Review the full text before editing it. Saving creates a custom persona that
   replaces the packaged persona. It is not added to the packaged text.
3. Keep the text focused on behavior a model can follow, such as tone, response
   structure, and how spoken answers should sound.
4. Select **Save prompt**. The badge changes to **Custom**, and the new persona
   is used for the next response without a restart.
5. Select **Reset to default** to remove the custom persona and show the
   packaged version again. The badge changes to **Default**.

You cannot save an empty custom prompt. Use **Reset to default** instead of
deleting all the text.

## Write Instructions That Work Well

| Prefer | Avoid |
|---|---|
| One clear rule per bullet | Long paragraphs with several rules |
| A request you can observe, such as `Start long answers with a short summary` | A vague request such as `Be better` |
| A stated priority when two preferences could conflict | Requests to bypass approvals or permissions |
| A stable preference for future turns | A one-time task that belongs in the current chat |

Do not repeat the same preference in several places. If Profile and standing
instructions disagree, a model may apply the wording inconsistently. Keep
current tasks in the conversation, structured personal facts in Profile, and
project knowledge in Wiki or Memory.

## How It Fits Together

1. You send a chat message or start a voice turn.
2. Jarvis loads either the packaged persona or your custom replacement, then
   adds the current standing instructions.
3. For normal brain turns, Jarvis can also add Profile details, relevant
   memory, conversation context, and the tools available for that turn.
4. The active provider and model receive that context and produce a response.
   Changing providers does not delete the locally saved layers, although a
   different model may follow the same wording differently.
5. Runtime controls add the actual assistant identity, provider and model
   context, tool boundaries, reply language, and safety rules. These controls
   take priority over either editor.
6. If an action is requested, its safety tier decides whether it can run,
   needs approval, or is blocked. Prompt text cannot change that decision.

Standing instructions override the packaged or custom persona on ordinary
style choices when they conflict. The explicit **Reply Language** setting and
runtime safety or tool rules still win. A custom System Prompt replaces the
packaged persona only; it does not replace the standing instructions or
runtime controls.

Direct Realtime voice responses receive the active persona and standing
instructions, but they do not currently receive the full Profile or Memory
context. A Realtime request delegated to the normal brain can use the context
available there. Read [Voice Conversations](voice-conversations) for the
difference between voice modes.

## Check That It Works

Save one harmless standing preference, such as asking for a short summary at
the start of longer answers. Send a new multi-part question in **Chats**. The
next answer should use that structure.

Clear the editor and select **Save instructions**. Confirm that the badge says
**Empty**, then start a new chat and repeat the question. The cleared preference
should no longer guide the answer.

For the persona editor, the visible check is the badge: **Save prompt** changes
it to **Custom**, and **Reset to default** changes it back to **Default**.

## Current Limitations

- Instructions are guidance to a model, not a formatting engine. Long,
  ambiguous, or conflicting rules may be followed inconsistently.
- Models can interpret the same text differently. Test important behavior
  again after changing the active model.
- Direct Realtime voice includes at most the first 4,000 characters of standing
  instructions and marks longer content as truncated.
- Direct Realtime voice does not currently receive the full Profile or Memory
  context.
- The editors do not provide automatic spell checking.
- Persona and instruction files are stored on this installation. They survive
  restarts and provider changes, but they are not copied automatically to
  another device.
- Changes affect later responses. They do not rewrite earlier messages or
  stored memories.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Save instructions** is unavailable | The editor has not changed, or the page is still loading | Make one small edit, wait for loading to finish, and try again |
| A language preference is ignored | The runtime **Reply Language** setting takes priority | Open **Settings > Languages > Reply Language** and choose the intended option |
| Another preference is ignored | The rule is vague, conflicts with another layer, or the model interpreted it differently | Keep one observable rule, remove duplicates, and test it in a new chat |
| Old behavior remains after clearing | The current conversation, Profile, Memory, or persona may still suggest it | Start a new chat and review each layer separately |
| The System Prompt cannot be saved | The editor is empty or the save request failed | Restore some text, or select **Reset to default** |
| Jarvis still asks for approval | The action is controlled by safety and permissions | Review [Safety and Approvals](safety-and-approvals); do not add bypass instructions |

If either editor shows an error or fails to load, keep your draft in a temporary
local note, reopen the view, and follow [Troubleshooting](troubleshooting)
before saving again.

## Next Steps

- Read [Chats](chats) to test a preference in a new conversation.
- Review [Profile and Contacts](profile-and-contacts) to keep structured facts
  about you and other people in the right place.
- Use [Wiki and Memory](wiki-and-memory) for project knowledge and facts you
  want Jarvis to recall when relevant.
- Read [Safety and Approvals](safety-and-approvals) to understand which action
  rules cannot be changed by instructions or a persona.
