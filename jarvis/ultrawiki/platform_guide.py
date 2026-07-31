"""Where each platform keeps its own export button, and what comes out.

The piece that makes "import everything from anywhere" true for services we
will never ship a connector for. A person who wants their WhatsApp history,
their Instagram years or their Spotify listening does not need us to integrate
with those companies — they need to know which menu the export lives in, what
lands in their inbox, and that dropping it here will work.

**Why this is a catalog and not a link list.** The awkward part of a
self-service export is never the download; it is finding the setting (four
levels deep, renamed every other year, in a different place on phone and web)
and knowing what to expect (a mail that arrives days later, a ZIP or a `.tgz`,
JSON or HTML). That knowledge is what the entries carry.

Universality — the constraint that shapes every entry (charter §3):

* **The route is the platform's OWN feature**, available worldwide. It is not
  built on the EU right to data portability. That right is often WHY the
  feature exists, but a person in São Paulo, Lagos or Jakarta clicks the same
  button, and an entry that only makes sense inside one legal regime would be
  useless to most of the people reading it. Where a statutory route is a real
  additional option, ``legal_route`` names it as an EXTRA, never as the path.
* **Only self-service.** No scraping recipe, no credential sharing, nothing
  that breaches a platform's terms. An export the platform itself offers is
  the whole scope.
* **No invented capability.** ``reads`` names the formats this importer
  actually handles today. An export we can only partly read says so.

Nothing here executes: it is data the UI renders and the CLI prints. Adding a
platform is one entry, no code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CATEGORIES",
    "PLATFORMS",
    "PlatformExport",
    "as_dict",
    "get_platform",
    "list_platforms",
    "search_platforms",
]

#: Grouping for display. Ordered by how much of a life the group usually
#: holds, which is also the order most people would look for them in.
CATEGORIES: tuple[str, ...] = (
    "messaging",
    "social",
    "email-and-files",
    "media",
    "work",
    "health-and-money",
)


@dataclass(frozen=True, slots=True)
class PlatformExport:
    """One platform's self-service export, described well enough to follow."""

    #: Stable id, used by the API and the CLI. Never shown raw to a user.
    id: str
    #: The vendor's own spelling of the product name. Never translated.
    label: str
    category: str
    #: The literal menu path, as the platform words it. The single most
    #: valuable field here: it is what turns "somewhere in settings" into a
    #: two-minute task.
    where: str
    #: What the export contains, in plain words.
    contains: str
    #: File formats that arrive.
    formats: tuple[str, ...]
    #: How long it typically takes to become available.
    wait: str
    #: What THIS importer will read out of it today.
    reads: str
    #: Anything that would otherwise be a nasty surprise.
    caveat: str = ""
    #: An additional, jurisdiction-specific route. Never the primary path.
    legal_route: str = ""
    #: Brand asset key, matching the frontend's `src/assets/brands` naming.
    #: Empty when no mark is bundled — the UI falls back to a monogram.
    brand: str = ""


PLATFORMS: tuple[PlatformExport, ...] = (
    # -- messaging ---------------------------------------------------------
    PlatformExport(
        id="whatsapp",
        label="WhatsApp",
        category="messaging",
        where=(
            "Open a chat, then the chat name or the ⋮ menu → More → Export "
            "chat. Repeat per conversation; there is no all-at-once export."
        ),
        contains="The messages of one conversation, optionally with its attachments.",
        formats=(".txt", ".zip"),
        wait="Immediate, on the phone.",
        reads=(
            "Fully. Messages are grouped into one item per chat and day, in "
            "any phone language, and attachments in the ZIP are imported too."
        ),
        caveat=(
            "Export without media unless you want the photos: with media a "
            "long chat can be several gigabytes."
        ),
        brand="whatsapp",
    ),
    PlatformExport(
        id="telegram",
        label="Telegram",
        category="messaging",
        where="Telegram Desktop → Settings → Advanced → Export Telegram data.",
        contains="Chats, contacts, and any media you tick.",
        formats=("JSON", "HTML"),
        wait="Minutes to a few hours; Telegram enforces a waiting period the first time.",
        reads="Fully — both the JSON and the HTML export.",
        caveat="Only the desktop app can export; there is no way to do it from a phone.",
        brand="telegram",
    ),
    PlatformExport(
        id="signal",
        label="Signal",
        category="messaging",
        where=(
            "Android: Settings → Chats → Chat backups. iOS has no message "
            "export; it can only transfer to a new phone."
        ),
        contains="An encrypted backup of your messages.",
        formats=(".backup",),
        wait="Immediate.",
        reads=(
            "Not yet. The backup is encrypted with a 30-digit passphrase and "
            "needs a dedicated decryptor before anything can read it."
        ),
        caveat="Stated plainly so nobody exports 40 GB expecting it to import.",
        brand="signal",
    ),
    PlatformExport(
        id="discord",
        label="Discord",
        category="messaging",
        where="Settings → Data & Privacy → Request all of my Data.",
        contains="Your messages, servers, and account activity.",
        formats=(".zip", "JSON"),
        wait="Up to 30 days — request it early.",
        reads="Fully.",
        brand="discord",
    ),
    PlatformExport(
        id="slack",
        label="Slack",
        category="messaging",
        where=(
            "Workspace owners: <workspace>.slack.com/services/export. Members "
            "can only export their own DMs on paid plans."
        ),
        contains="Public channel history; private channels and DMs depend on the plan.",
        formats=(".zip", "JSON"),
        wait="Minutes to hours depending on size.",
        reads="Fully.",
        caveat="What you are allowed to export is decided by the workspace plan, not by you.",
        brand="slack",
    ),
    # -- social ------------------------------------------------------------
    PlatformExport(
        id="instagram",
        label="Instagram",
        category="social",
        where=(
            "Settings → Accounts Centre → Your information and permissions → "
            "Download your information."
        ),
        contains="Posts, stories, messages, comments, likes, and your profile.",
        formats=(".zip", "JSON", "HTML"),
        wait="A few hours to 14 days; a mail arrives with the link.",
        reads=(
            "Messages, posts and comments as text; photos and videos are "
            "captured with their date and queued to be described."
        ),
        caveat="Pick JSON over HTML — it imports far more cleanly.",
        brand="instagram",
    ),
    PlatformExport(
        id="facebook",
        label="Facebook",
        category="social",
        where=(
            "Settings & privacy → Settings → Your information → Download your "
            "information."
        ),
        contains="Posts, messages, photos, events, groups, and your whole history.",
        formats=(".zip", "JSON", "HTML"),
        wait="Hours to several days.",
        reads="Messages, posts and comments as text; photos with their dates.",
        caveat="Select date ranges rather than 'all time' if you only want recent years.",
        brand="facebook",
    ),
    PlatformExport(
        id="x-twitter",
        label="X (Twitter)",
        category="social",
        where="Settings and privacy → Your account → Download an archive of your data.",
        contains="Your posts, direct messages, likes and profile.",
        formats=(".zip", "JS/JSON"),
        wait="Around 24 hours.",
        reads="Fully — the archive's data files import as records.",
        caveat="Requires re-entering your password and a confirmation code.",
        brand="x",
    ),
    PlatformExport(
        id="tiktok",
        label="TikTok",
        category="social",
        where="Profile → Settings and privacy → Account → Download your data.",
        contains="Your videos, comments, likes, messages and watch history.",
        formats=(".zip", "JSON", "TXT"),
        wait="A few days.",
        reads="Text and records fully; videos are captured and queued for transcription.",
        caveat="Choose the JSON option — the TXT one is much harder to read back.",
        brand="tiktok",
    ),
    PlatformExport(
        id="reddit",
        label="Reddit",
        category="social",
        where="Settings → Privacy & Security → Request a copy of your data.",
        contains="Your posts, comments, votes, saved items and messages.",
        formats=(".zip", "CSV"),
        wait="Up to 30 days.",
        reads="Fully.",
        brand="reddit",
    ),
    PlatformExport(
        id="linkedin",
        label="LinkedIn",
        category="social",
        where="Settings & Privacy → Data privacy → Get a copy of your data.",
        contains="Your profile, connections, messages, posts and job applications.",
        formats=(".zip", "CSV"),
        wait="Minutes for the basics, up to 24 hours for the full archive.",
        reads="Fully.",
        caveat="The fast 'basic' export omits messages; tick the full archive if you want them.",
        brand="linkedin",
    ),
    # -- email and files ---------------------------------------------------
    PlatformExport(
        id="google",
        label="Google (Takeout)",
        category="email-and-files",
        where="takeout.google.com — pick the services, then Export once.",
        contains=(
            "Whatever you tick: Gmail, Drive, Calendar, Contacts, Photos, "
            "YouTube, Maps timeline, Chrome history, and more."
        ),
        formats=(".zip", ".tgz", "mbox", "iCalendar", "vCard", "JSON"),
        wait="Hours to several days for a large account.",
        reads=(
            "Fully. Mail, calendar, contacts, documents and data files all "
            "import; photos are captured with their capture date and place."
        ),
        caveat=(
            "Both the .zip and the .tgz download are read. Split large "
            "exports into 2 GB parts and drop the whole folder at once."
        ),
        brand="google",
    ),
    PlatformExport(
        id="apple",
        label="Apple",
        category="email-and-files",
        where="privacy.apple.com → Request a copy of your data.",
        contains="iCloud Mail, photos, notes, calendars, contacts, purchases and device data.",
        formats=(".zip", "CSV", "vCard", "iCalendar"),
        wait="Up to 7 days.",
        reads="Mail, calendars, contacts and notes fully; photos with their dates.",
        caveat="Downloads expire after roughly two weeks.",
        brand="apple",
    ),
    PlatformExport(
        id="microsoft",
        label="Microsoft / Outlook",
        category="email-and-files",
        where=(
            "account.microsoft.com/privacy → Download your data. Outlook mail "
            "itself exports from Outlook → File → Open & Export."
        ),
        contains="Account activity, and — from Outlook — your mailbox.",
        formats=(".zip", ".pst", "CSV"),
        wait="Minutes to a few days.",
        reads=(
            "The data archive fully. A `.pst` mailbox is NOT readable — export "
            "the mail as `.eml` files or an `.mbox` instead."
        ),
        caveat="`.pst` is a proprietary database, not a mail archive.",
        brand="microsoft",
    ),
    PlatformExport(
        id="dropbox",
        label="Dropbox",
        category="email-and-files",
        where="Just download the folders from the desktop app or the website.",
        contains="Your files, exactly as they are.",
        formats=("any",),
        wait="Immediate.",
        reads="Fully — documents, PDFs, spreadsheets, notes, and photos.",
        caveat=(
            "A plain folder, so the Local folder source imports it directly "
            "and keeps it up to date."
        ),
        brand="dropbox",
    ),
    PlatformExport(
        id="notion",
        label="Notion",
        category="email-and-files",
        where="Settings → Workspace → Settings → Export all workspace content.",
        contains="Every page, database and file in the workspace.",
        formats=(".zip", "Markdown", "CSV", "HTML"),
        wait="Minutes to hours; a mail arrives with the link.",
        reads="Fully. Choose Markdown & CSV.",
        brand="notion",
    ),
    PlatformExport(
        id="evernote",
        label="Evernote",
        category="email-and-files",
        where="Right-click a notebook → Export notes, or File → Export.",
        contains="Notes with their attachments.",
        formats=(".enex", "HTML"),
        wait="Immediate.",
        reads=(
            "The HTML export fully. `.enex` is an XML format we do not parse "
            "yet — export as HTML for now."
        ),
        brand="evernote",
    ),
    # -- media -------------------------------------------------------------
    PlatformExport(
        id="spotify",
        label="Spotify",
        category="media",
        where="Account → Privacy settings → Download your data.",
        contains="Playlists, listening history, searches and library.",
        formats=(".zip", "JSON"),
        wait="Up to 30 days for the extended history; days for the basics.",
        reads="Fully.",
        caveat="Tick 'Extended streaming history' if you want more than the last year.",
        brand="spotify",
    ),
    PlatformExport(
        id="netflix",
        label="Netflix",
        category="media",
        where="Account → Security & Privacy → Personal information access.",
        contains="Viewing history, ratings, searches and account details.",
        formats=(".zip", "CSV"),
        wait="Up to 30 days.",
        reads="Fully.",
        brand="netflix",
    ),
    PlatformExport(
        id="youtube",
        label="YouTube",
        category="media",
        where="Through takeout.google.com — select YouTube and YouTube Music.",
        contains="Watch history, subscriptions, playlists, comments and your own videos.",
        formats=(".zip", "JSON", "CSV"),
        wait="Hours to days.",
        reads="History, playlists and comments fully.",
        caveat="Untick 'videos' unless you want tens of gigabytes.",
        brand="youtube",
    ),
    PlatformExport(
        id="goodreads",
        label="Goodreads",
        category="media",
        where="My Books → Import and export → Export Library.",
        contains="Every book you shelved, with ratings, dates and reviews.",
        formats=("CSV",),
        wait="Minutes.",
        reads="Fully.",
        brand="goodreads",
    ),
    PlatformExport(
        id="pocket",
        label="Pocket / read-later",
        category="media",
        where="getpocket.com/export (or the equivalent in Instapaper, Raindrop, Matter).",
        contains="Everything you saved to read later, with tags.",
        formats=("HTML", "CSV"),
        wait="Immediate.",
        reads="Fully.",
    ),
    # -- work --------------------------------------------------------------
    PlatformExport(
        id="github",
        label="GitHub",
        category="work",
        where="Settings → Account → Export account data.",
        contains="Your repositories, issues, comments and account metadata.",
        formats=(".tar.gz", "JSON"),
        wait="Up to 7 days; a download link arrives by mail.",
        reads="Fully — the `.tar.gz` archive is read directly.",
        brand="github",
    ),
    PlatformExport(
        id="jira",
        label="Jira / Confluence",
        category="work",
        where="Admin → System → Export, or per project: Issues → Export → CSV.",
        contains="Issues, comments and pages.",
        formats=("CSV", "XML", ".zip"),
        wait="Minutes to hours.",
        reads="CSV fully; a full XML site backup is not parsed.",
        caveat="Site-wide exports usually need an administrator.",
    ),
    PlatformExport(
        id="trello",
        label="Trello",
        category="work",
        where="Open a board → Show menu → More → Print and export → Export as JSON.",
        contains="One board with its cards, lists, comments and attachments.",
        formats=("JSON", "CSV"),
        wait="Immediate.",
        reads="Fully.",
        caveat="One board at a time unless you are on a paid workspace.",
        brand="trello",
    ),
    PlatformExport(
        id="asana",
        label="Asana",
        category="work",
        where="Project dropdown → Export/Print → JSON or CSV.",
        contains="Tasks, comments, assignees and dates for one project.",
        formats=("JSON", "CSV"),
        wait="Minutes; a mail arrives with the file.",
        reads="Fully.",
        brand="asana",
    ),
    PlatformExport(
        id="zoom",
        label="Zoom",
        category="work",
        where="Recordings → download the recording and its transcript.",
        contains="Meeting recordings, chat logs and auto-transcripts.",
        formats=(".mp4", ".vtt", ".txt"),
        wait="Immediate once processing finishes.",
        reads=(
            "Transcripts and chat logs fully; recordings are captured and "
            "queued for transcription."
        ),
        caveat="Cloud recordings are deleted on a schedule — download them before they go.",
    ),
    # -- health and money --------------------------------------------------
    PlatformExport(
        id="strava",
        label="Strava",
        category="health-and-money",
        where="Settings → My Account → Download or Delete Your Account → Request your archive.",
        contains="Every activity with its route, plus posts and comments.",
        formats=(".zip", "CSV", "GPX"),
        wait="A few hours.",
        reads="The activity records and CSV fully; GPX routes are stored as data files.",
        brand="strava",
    ),
    PlatformExport(
        id="apple-health",
        label="Apple Health",
        category="health-and-money",
        where="Health app → your profile picture → Export All Health Data.",
        contains="Every health and fitness record on the device.",
        formats=(".zip", "XML"),
        wait="Several minutes on the phone.",
        reads=(
            "Partly. The XML is one enormous file; it imports as data rather "
            "than as readable entries."
        ),
        caveat="Can be over a gigabyte after a few years of use.",
    ),
    PlatformExport(
        id="google-fit",
        label="Google Fit / Fitbit",
        category="health-and-money",
        where="Through takeout.google.com — select Fit, or Fitbit → Settings → Data Export.",
        contains="Daily activity, sleep, heart rate and workouts.",
        formats=(".zip", "CSV", "JSON"),
        wait="Hours.",
        reads="Fully.",
    ),
    PlatformExport(
        id="banking",
        label="Your bank",
        category="health-and-money",
        where=(
            "Almost every online bank has 'Export' or 'Download' on the "
            "transactions page. Look for CSV."
        ),
        contains="Transactions for the period you select.",
        formats=("CSV", ".pdf", "CAMT/MT940"),
        wait="Immediate.",
        reads="CSV and PDF statements fully.",
        caveat=(
            "Financial records are among the most sensitive things you own. "
            "Import them only into a knowledge base whose storage and "
            "providers you are comfortable with."
        ),
    ),
    PlatformExport(
        id="amazon",
        label="Amazon",
        category="health-and-money",
        where="Account → Data Privacy → Request Your Data.",
        contains="Order history, searches, Kindle notes, Alexa recordings and reviews.",
        formats=(".zip", "CSV", "JSON"),
        wait="Up to 30 days.",
        reads="Fully.",
        caveat="Request only the categories you want — 'everything' takes far longer.",
        brand="amazon",
    ),
)


_BY_ID: dict[str, PlatformExport] = {entry.id: entry for entry in PLATFORMS}


def list_platforms(category: str = "") -> tuple[PlatformExport, ...]:
    """Every entry, or only those in one category. Unknown category → empty."""
    wanted = str(category or "").strip().lower()
    if not wanted:
        return PLATFORMS
    return tuple(entry for entry in PLATFORMS if entry.category == wanted)


def get_platform(platform_id: str) -> PlatformExport | None:
    """One entry by id, or ``None``."""
    return _BY_ID.get(str(platform_id or "").strip().lower())


def search_platforms(query: str) -> tuple[PlatformExport, ...]:
    """Entries matching a free-text query, best matches first.

    Deliberately simple substring matching over the name, id and description:
    the catalog is thirty rows, so anything cleverer would be machinery
    without a purpose. A name match outranks a description match, because
    someone typing "photos" wants Google and Apple before a caveat that
    happens to mention the word.
    """
    needle = str(query or "").strip().lower()
    if not needle:
        return PLATFORMS
    by_name: list[PlatformExport] = []
    by_text: list[PlatformExport] = []
    for entry in PLATFORMS:
        if needle in entry.label.lower() or needle in entry.id:
            by_name.append(entry)
            continue
        haystack = " ".join(
            (entry.contains, entry.where, entry.reads, entry.caveat, entry.category)
        ).lower()
        if needle in haystack:
            by_text.append(entry)
    return tuple(by_name + by_text)


def as_dict(entry: PlatformExport) -> dict[str, Any]:
    """The API payload of one entry."""
    return {
        "id": entry.id,
        "label": entry.label,
        "category": entry.category,
        "where": entry.where,
        "contains": entry.contains,
        "formats": list(entry.formats),
        "wait": entry.wait,
        "reads": entry.reads,
        "caveat": entry.caveat,
        "legal_route": entry.legal_route,
        "brand": entry.brand,
    }
