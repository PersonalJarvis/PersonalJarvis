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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [profile, contacts, personalization, privacy]
related: [instructions-and-persona, wiki-and-memory, privacy-and-local-data]
---

Use **Profile** for structured facts and preferences about you. Use
**Contacts** as an address book for people you may want Jarvis to look up,
message, or call. Both are saved in your local Jarvis data, but they serve
different purposes.

The **People Jarvis knows** section in Profile is separate from Contacts. It is
a read-only summary of people learned through memory features, not an address
book that you manage there.

## Choose the Right Place

| Place | Store this here | What Jarvis can use it for |
|---|---|---|
| **Profile** | Your identity, communication preferences, work style, values, and feedback style | Personal context for later replies |
| **Contacts** | Another person's name, aliases, relationship, email addresses, phone numbers, postal address, and short README note | Name matching and contact details for supported actions |
| **People Jarvis knows** in Profile | Names, aliases, and relationships learned by the memory system | A summary of people in your life; you cannot edit contact details here |
| **Wiki/Memory** | Longer notes, projects, decisions, and connected knowledge | Searchable long-term context, including person pages when that feature is active |

Profile has the following structured fields:

| Group | Fields shown in the app |
|---|---|
| **Identity** | Name, Address form, Pronouns, Primary language, Languages, Timezone, Devices |
| **Communication** | Directness (1-5), Formality (1-5), Verbosity, Humor styles, Emojis OK? |
| **Work Style** | Focus mode, Planning horizon |
| **Values** | Top values, Pet peeves, Motivations |
| **Relationship** | Feedback style |

These language fields are profile facts. They do not replace the app's language
and voice settings. The conversation prompt gives priority to useful profile
details such as your name, preferred form of address, communication style,
values, work style, and feedback style. A saved field provides context; it does
not guarantee that every reply will mention it.

Your profile photo is only shown in Profile. It is not an identity check or
conversation input, and it does not change Jarvis's answers.

## Update Your Profile

1. Open **Profile**. The page groups saved information under **Identity**,
   **Communication**, **Work Style**, **Values**, and **Relationship**.
2. Find the row you want to change and activate its **Edit** control. The app
   shows every saved field and up to two empty fields in each group. More empty
   fields appear as you fill the visible ones.
3. Enter a value, choose **Yes** or **No**, or add and remove list items. The
   editor type depends on the field.
4. Activate **Save**. The saved value appears in Profile and can provide context
   on later turns.
5. To add a picture, activate the profile image or **Upload photo**. You can
   later use **Change photo** or **Remove photo**.

Photo uploads accept PNG, JPEG, WebP, and GIF files up to 8 MB. Uploading a new
photo replaces the previous one.

Jarvis can also save a durable fact that you state clearly in chat or voice,
such as how you want to be addressed. This path needs an active assistant that
can use the profile update action. A successful update is saved directly; it
does not wait in the review queue. Check Profile after an important update
instead of assuming that the request was stored. Temporary states, one-off
requests, and selected sensitive categories are not stored in Profile.

If **Waiting for your OK** contains suggestions, review the displayed evidence
and value before choosing **Confirm** or **Strike out**. The current Wiki/Memory
setup may not use this queue, so an inactive or empty queue does not prevent
manual Profile edits.

> [!warning] **The source file** is an advanced editor for the complete
> `USER.md` profile. Invalid YAML in its structured header can leave the
> Profile cards empty. Use the row-level **Edit** controls for normal changes.

## Add and Manage Contacts

1. Open **Contacts** and choose **Add contact**.
2. Enter a name. Add aliases for nicknames or other spellings that you use for
   the same person.
3. Optionally choose **Family**, **Friend**, **Colleague**, **Partner**,
   **Acquaintance**, or **Other**. Add only the email addresses, phone numbers,
   postal address, and **README** note that you need.
4. Choose **Save**. The contact appears in the list and opens in the detail
   pane.
5. Choose **Edit** to change the record. Choose **Delete**, then confirm, to
   remove it from Contacts.

**Search contacts…** checks names and aliases, including partial matches. It
does not search email addresses, phone numbers, postal addresses, or README
text.

When the active assistant can use contact tools, you can explicitly ask in
chat or voice to save a relationship, email address, phone number, postal
address, or note. Jarvis updates an exact name or alias match when possible;
otherwise it creates a contact. A spoken postal address is stored as one value
in the **Street** field. Open the form if you want to split it into **Street**,
**Postal code**, **City**, and **Country**. Contact deletion remains an in-app
action.

The Contacts page and its local records do not depend on a brain provider. If
contact tools are unavailable in a conversation, you can still add, edit,
search, and delete contacts in the app.

## Privacy and Control

Profile, Contacts, and your profile photo are stored in the Jarvis data folder
for the current device. Jarvis does not copy them to another installation as
part of a software update. A connected or externally synced Wiki vault follows
that vault's own sync rules.

Local storage does not mean that every use stays on the device:

- Profile context may be included when Jarvis sends a conversation to the
  active brain provider.
- A contact index containing names, aliases, and relationships may also be
  included. Email addresses, phone numbers, postal addresses, and README notes
  are looked up only when an action needs the full record.
- Sending a message, email, or phone call can pass the required detail to the
  connected service that performs the action.
- **The source file** displays the full profile contents and the safe filename
  `USER.md`. Crop personal values and observations from shared screenshots.
- Never store passwords, API keys, recovery codes, or other credentials in a
  profile field, contact note, or contact detail.

When Wiki/Memory is active, each saved contact can be mirrored to a person page
with the contact's name, aliases, relationship, and README note. Email
addresses, phone numbers, and postal addresses are deliberately excluded from
that page. Keep sensitive details in their dedicated contact fields, because
the README note is mirrored.

Deleting a contact removes its address-book record. If it has a Wiki/Memory
person page, that page is archived so notes learned or added outside the
contact-managed section are not destroyed. Review Wiki/Memory too when you want
to remove all information about someone. Existing chats and records held by
connected services are separate and are not deleted with the contact.

## How It Fits Together

1. You edit a Profile field or Contact record, or you clearly ask Jarvis to
   save a fact during a conversation.
2. Jarvis writes facts about you to Profile and address-book details about
   another person to Contacts. The two stores do not overwrite each other.
3. Later conversations can receive selected Profile context and the contact
   name index. Full contact details are fetched only when an action needs them.
4. Persona defines Jarvis's baseline character, while Instructions are standing
   rules that you write. Neither one is automatically copied into Profile.
5. Wiki/Memory can add longer context and mirror a privacy-limited person page
   for a contact. It does not replace Contacts or rewrite Profile fields.
6. If a conversation cannot use the required profile or contact action, edit
   the record in the app. Manual Contacts management continues to work without
   a brain provider.

## Check That It Works

1. In **Profile**, edit **Address form** and save a non-sensitive temporary
   value. Activate **Reload** and confirm the value remains. Restore your
   preferred value afterward.
2. In **Contacts**, add a temporary contact with only a name and alias. Search
   for part of the alias, open the result, and confirm it is the same record.
   Delete the temporary contact when finished.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Profile shows a not-ready or `USER.md` error | The profile subsystem has not finished starting, or the profile file is unavailable | Wait for startup to finish, activate **Reload**, and try again. Contacts remains available separately |
| A Profile field is not visible, or a stated fact was not saved | Only two empty fields per group are shown at once, or the assistant did not complete the profile update | Fill a visible field to reveal more rows, or use its guided prompt. For an important fact, use the row-level **Edit** control and verify the saved value |
| **Waiting for your OK** is inactive or empty | This memory setup does not use the review queue, or it has no uncertain suggestions | Continue using Profile normally; manual edits do not depend on this queue |
| Contact search returns no result | Search checks only names and aliases | Clear the search, check the saved spelling, and add the expected alias with **Edit** |
| A deleted contact still appears in Wiki/Memory | Its person page was archived to preserve notes outside the contact-managed section | Review the archived page and follow the privacy guide before removing more data |

## Next Steps

- Read [Instructions and Persona](instructions-and-persona) to keep standing
  rules separate from facts stored in Profile.
- Read [Wiki and Memory](wiki-and-memory) to understand person pages and longer
  term knowledge.
- Read [Privacy and Local Data](privacy-and-local-data) before storing personal
  details or connecting a service that may process them.
