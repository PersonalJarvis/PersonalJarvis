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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [profile, contacts, personalization, privacy]
related: [instructions-and-persona, wiki-and-memory, privacy-and-local-data]
---

Use **Profile** for facts and preferences about you. Use **Contacts** for the
people you may ask Jarvis to recognize, message, or call. Keeping those records
separate prevents a friend's details from becoming part of your own profile.

Both features can make conversations more relevant, but they affect different
parts of Jarvis. You remain in control: you can review, edit, or remove the
stored information in the app.

## Choose the Right Place

| Place | What belongs there | What it affects | What it does not change |
|---|---|---|---|
| **Profile** | Your name, preferred form of address, languages, communication style, work style, values, and feedback preferences | The personal context Jarvis uses when replying to you | Jarvis's Persona, standing Instructions, contact records, or what your profile photo means |
| **Contacts** | Another person's name, aliases, relationship, email addresses, phone numbers, postal address, and a short note | Name matching and on-demand details for supported actions such as sending a message or starting a call | How Jarvis addresses you or the personality and tone defined for Jarvis |
| **People around you** in Profile | People mentioned in conversations and observations learned about them | Broader conversational memory about people in your life | The user-managed Contacts address book; it is not a reliable source of contact details |
| **Wiki/Memory** | Longer notes, projects, decisions, and connected knowledge | Searchable long-term context | Structured Profile fields; editing a wiki note does not necessarily update Profile |

Your profile photo is only used to represent you in the Profile view. It is not
an identity check and does not change Jarvis's answers.

## Update Your Profile

1. Open **Profile** from the app navigation. The page shows what Jarvis
   currently knows about you, grouped into identity, communication, work style,
   values, and relationship preferences.
2. Find the field you want to change. Activate **Edit** on that row; keyboard
   users can focus the row's edit control without relying on hover.
3. Enter a value, choose **Yes** or **No**, or add and remove list items,
   depending on the field.
4. Save the change. The field updates in the Profile view and becomes available
   to later conversations.
5. To add or replace your picture, use **Upload photo** or **Change photo**.
   Use **Remove photo** to return to your initials or the default profile icon.

Jarvis can also record a durable fact you clearly state in chat or voice, such
as how you want to be addressed. This requires the current assistant setup to
support the profile-update action. Check Profile afterward instead of assuming
that every sentence was saved. One-off requests and temporary states do not
belong in Profile, and automatic updates reject selected sensitive categories.

If **Waiting for your OK** contains suggestions, review the evidence and value
before choosing **Confirm** or **Strike out**. Some memory setups do not use a
review queue; an inactive or empty queue does not stop manual profile editing.

> [!warning] The **source file** editor is an advanced view. Its structured
> header drives the Profile cards, so an invalid edit can make those cards look
> empty. Prefer the field-level **Edit** controls for normal changes.

## Add and Manage Contacts

1. Open **Contacts** and choose **Add contact**.
2. Enter a name. Add aliases when you commonly use another spelling or
   nickname; Jarvis can use them to find the same record.
3. Optionally choose a relationship and add only the email addresses, phone
   numbers, postal address, and short **README** note you actually need.
4. Choose **Save**. The new contact appears in the list and its details open in
   the main pane.
5. Select **Edit** to change the record. Choose **Delete** and confirm to remove
   it from the address book.

**Search contacts…** matches names and aliases. It does not search email
addresses, phone numbers, addresses, or note text.

When the active assistant can use tools, you can explicitly ask in chat or
voice to save or update a person's relationship, email address, phone number,
or note. Jarvis matches an existing name or alias where possible and otherwise
creates a contact. Use the in-app form for postal addresses; conversation-based
postal-address updates are not currently reliable. Deleting a contact is an
in-app action. Always open Contacts to verify an important change.

The Contacts view and its local records do not depend on a brain provider. If
conversation tools are unavailable, you can still add, edit, search, and delete
contacts manually.

## Privacy and Control

Profile and Contacts are stored with your local Jarvis data. That does not mean
the information can never be processed elsewhere:

- Profile context and a compact contact index containing names, aliases, and
  relationships may be included when Jarvis sends a conversation to the active
  brain provider.
- Email addresses, phone numbers, and postal addresses are not added to every
  conversation. Jarvis retrieves them on demand when a supported action needs
  them.
- Completing an email, message, or phone action may send the required detail to
  the connected service that performs that action.
- The Profile source-file card currently shows its local file location. Treat
  that location as personal information and crop it from shared screenshots.
- Never store passwords, API keys, recovery codes, or other credentials in a
  profile field, contact note, or contact detail.

If Wiki/Memory is active, a saved contact can receive a person page containing
the name, aliases, relationship, and README note. Email addresses, phone
numbers, and street addresses are deliberately kept out of that wiki page.
Because the README note can be mirrored, keep sensitive details in the dedicated
contact fields rather than in the note.

Deleting a contact removes the address-book record. A related Wiki/Memory page
is archived rather than destroyed so separately learned notes are not lost.
Review Wiki/Memory as well when your goal is to remove all information about a
person. Existing chats, service history, and provider records are separate and
are not erased by deleting a contact.

## How It Fits Together

1. **You provide the input.** You edit Profile or Contacts directly, or state a
   durable fact in a chat or voice conversation.
2. **Jarvis keeps the subjects separate.** Facts about you go to Profile. A
   saved person's address-book details go to Contacts. Broader observations can
   go to Wiki/Memory or the **People around you** list.
3. **Chats receive useful context.** Profile supplies compact personal
   preferences. Contacts supplies a compact index of names, aliases, and
   relationships; full details are looked up only for an action that needs
   them.
4. **Instructions and Persona keep their own roles.** Persona defines the
   assistant's baseline character. Instructions are standing rules you write.
   Profile describes you. If the same preference appears in more than one
   place, make the wording consistent instead of relying on an assumed winner.
5. **Wiki/Memory adds depth.** It stores longer, connected knowledge and may
   mirror a privacy-limited person page for a contact. It does not replace the
   address book or automatically rewrite structured Profile fields.
6. **Availability degrades safely.** Manual Contacts management still works
   without a provider. If the active assistant cannot use a profile or contact
   tool, edit the record in the app and try the conversation again.

## Check That It Works

1. In **Profile**, set **Address form** to a non-sensitive temporary label
   with the row-level **Edit** control. Start a new chat and ask how Jarvis
   should address you. Success means the saved value appears in Profile and the
   reply recognizes it. Restore your preferred value afterward.
2. In **Contacts**, add a temporary contact with only a name and alias. Search
   for the alias, open the result, and confirm it shows the same record. Delete
   the temporary contact when finished.

These checks prove the two stores separately: a Profile change affects context
about you, while an alias helps Jarvis find a saved person.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Profile system not ready** | The active brain and structured profile have not finished starting, or this session does not provide them | Wait for startup to finish, use **Reload**, and try again; Contacts can still be managed separately |
| A fact stated in chat does not appear in Profile | The statement was temporary, blocked by the profile privacy rules, or the active assistant could not use the update action | Add the fact with the row-level **Edit** control and confirm it appears |
| **Waiting for your OK** is inactive or empty | The current memory setup writes elsewhere or has no uncertain suggestions | Continue using Profile normally; do not wait for a review to edit a field |
| Contact search shows no result | Search checks only names and aliases | Clear the search, confirm the saved spelling, and add the expected alias with **Edit** |
| Jarvis cannot use a saved person's details | The name did not match, the field is empty, or the required contact/action tool is unavailable | Open the contact, verify its alias and required detail, then retry or perform the action manually |
| A postal address stated in chat is not saved | The conversation-based address writer does not currently match the structured address record | Open **Contacts**, choose **Edit**, and enter the address in the labeled fields |
| A deleted contact still appears in Wiki/Memory | The wiki person page was archived to preserve separately learned notes | Review the archived wiki page and follow the privacy guide before removing additional data |

## Next Steps

- Read [Instructions and Persona](instructions-and-persona) to separate explicit
  standing rules from the facts and preferences held in Profile.
- Read [Wiki and Memory](wiki-and-memory) to understand longer-term knowledge,
  mirrored person pages, and what deleting a contact does not remove.
- Read [Privacy and Local Data](privacy-and-local-data) before storing personal
  details or connecting a service that may process them.
