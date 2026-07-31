---
title: "Profile and Contacts"
slug: profile-and-contacts
summary: "Keep your own preferences and the people you mention organized, and understand how both support more relevant conversations."
section: "Everyday use"
section_order: 2
order: 7
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [profile, contacts, personalization, privacy]
related: [instructions-and-persona, wiki-and-memory, privacy-and-local-data]
---

Use **Profile** for facts and preferences about you. Use **Contacts** for people
your assistant may recognize, message, or call. Wiki context replaces neither.

## Choose the Authoritative Place

| Place | What belongs there | Important boundary |
|---|---|---|
| **Profile** | Your identity, languages, devices, communication and work style, values, and feedback preference | Authoritative for these structured preferences |
| **Contacts** | Another person's name, aliases, relationship, emails, phones, postal address, and short README note | Edit the contact here even if a Wiki page also exists |
| **People your assistant knows** in Profile | A read-only list of people learned by older memory features | It is not Contacts and cannot edit contact details |
| **Normal Wiki** | Long-form knowledge about people, projects, decisions, and sources | Its prose can overlap with Profile but does not rewrite it |
| **UltraWiki People** | Evidence-linked identities, identifiers, events, and merge decisions | It is a research view, not a Profile or Contacts editor |

Profile language fields do not change the app's language or voice settings.
Your photo is shown in Profile, but is not an identity check or conversation
context.

## Update Your Profile

1. Open **Profile** and find the relevant group: **Identity**,
   **Communication**, **Work Style**, **Values**, or **Relationship**.
2. Choose **Edit**, enter the value, and choose **Save**. Filling a visible
   empty field reveals more fields in that group.
3. Clear values or remove list items when they are no longer correct.
4. Choose the image or **Upload photo** to add, change, or remove a photo.

You can ask your assistant in chat or voice to remember a durable fact, such as
how you prefer to be addressed. This requires the Profile action. Verify
important changes in Profile. Temporary requests and selected sensitive
categories are not saved there.

For **Waiting for your OK** suggestions, check the evidence before **Confirm**
or **Strike out**. Some Wiki setups do not use this queue; Profile editing
still works.

> [!warning] **The source file** is an advanced editor for the complete
> `USER.md` profile. A malformed structured header can make the Profile cards
> appear empty. Prefer the normal field editors.

## Add, Edit, and Delete Contacts

1. Open **Contacts**, choose **Add contact**, and enter a name.
2. Add useful aliases, relationship, emails, phones, address, or README note.
3. Choose **Save**. Use **Edit** or **Delete** for later changes.

Search checks partial names and aliases, not emails, phones, addresses, or
README text. When names match, inspect the record before editing.

You can ask your assistant to save contact details. An exact name or alias
match is updated; otherwise a contact is created. A spoken address may appear
entirely in **Street**; use the form to split it. Delete contacts in the app.
Manual management works without a brain provider.

Contacts has no CSV, vCard, or phone import. UltraWiki's **Import address
book** copies existing Contacts into UltraWiki, not into Contacts.

## Understand the Wiki Relationships

In **Normal Wiki** mode, your assistant can maintain a longer living page about
you, including preferences, relationships, projects, decisions, and sources.
Profile remains authoritative for structured fields. If they disagree, correct
Profile and review the Wiki separately.

Normal Wiki creates a Contact companion page with name, aliases, relationship,
and README note, but no email, phone, or postal address. Edit the Contact, not
the managed page section. Deletion archives the page to preserve other notes.

In **UltraWiki** mode, **Explore → People** shows identities built from approved
sources and evidence, including identifiers, events, open questions, and merge
history.

To seed UltraWiki from Contacts:

1. Review the records in **Contacts**.
2. In **UltraWiki → Explore → People**, choose **Import address book**.
3. Review the report and **Open questions**.
4. Confirm only evidence-backed same-person merges; reject different people.
   Undo confirmed merges from merge history when needed.

The import copies names, aliases, emails, phones, and a stable contact identity,
but not relationship, postal address, or README notes. Rerunning it safely adds
current identifiers. It neither syncs changes back nor removes UltraWiki
evidence after Contact deletion, so review both places.

## Privacy and Provider Processing

Profile, Contacts, and the photo stay in this installation's data folder and do
not travel with an update. Connected Wiki storage follows its own sync rules.

The brain provider can receive selected Profile context and Contact names,
aliases, and relationships. Full details are fetched when an action needs them
and may reach its message, email, or call service. Normal Wiki providers can
process selected excerpts.

UltraWiki contact seeding needs no AI model. A remote database stores imported
identifiers remotely, while later search, Ask, extraction, or ranking can send
selected material to configured providers. A local database alone does not
guarantee local-only processing.

> [!warning] Never put passwords, API keys, recovery codes, private keys, or
> other credentials in Profile, Contacts, Normal Wiki, or UltraWiki. Add
> provider credentials only through the protected connection screens. See
> [Privacy and Local Data](privacy-and-local-data) before storing personal data.

Clearing a Profile field does not remove matching Wiki prose. Deleting a
Contact does not delete chats, service records, an UltraWiki identity, or its
merge audit. Review every relevant system for complete removal.

## How It Fits Together

1. Profile supplies structured conversation preferences.
2. Contacts supplies a name index; actions fetch details when needed.
3. Normal Wiki adds long-form context and limited companion pages.
4. UltraWiki connects people through evidence and reviewed merges.
5. No store automatically becomes authoritative for another.

Persona sets your assistant's baseline character, while Instructions are your
standing rules. Neither is automatically copied into Profile or Contacts.

## Check That It Works

1. Edit a non-sensitive Profile field, reload, and confirm it remains.
2. Add a temporary Contact and alias, search it, then delete it.
3. In UltraWiki, import the address book and check the report.

## Troubleshooting

| What you see | What to do |
|---|---|
| Profile reports that `USER.md` is unavailable | Wait for startup, choose **Reload**, and try again. Contacts remains separate |
| A Profile field is missing | Save one of the visible empty fields to reveal more, or use its guided prompt |
| A spoken Profile change did not appear | The assistant may not have had the Profile action. Edit the field directly and verify it |
| **Waiting for your OK** is empty | Continue normally; this setup may write approved knowledge to its Wiki instead |
| Contact search misses a known detail | Search by name or alias, then open the record to inspect other fields |
| A deleted Contact still has a Normal Wiki page | The companion page was archived to preserve independent notes; review the archive separately |
| An UltraWiki import created an open question | Compare the evidence, then confirm or reject the proposed identity merge |
| A Contact edit is absent from UltraWiki | Run **Import address book** again; UltraWiki does not continuously mirror Contacts |

## Next Steps

- Read [Instructions and Persona](instructions-and-persona) for standing rules
  and assistant behavior.
- Read [Wiki and Memory](wiki-and-memory) for Normal Wiki context and person
  pages.
- Read [UltraWiki](ultrawiki) for evidence, identity review, and merge history.
- Read [Privacy and Local Data](privacy-and-local-data) before connecting a
  provider or storing sensitive personal information.
