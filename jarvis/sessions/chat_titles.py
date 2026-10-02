"""Titles for the chat history: a topic, never the first thing someone said.

The sidebar used to show each conversation's opening words. Spoken chats open
with "Hey George, was geht ab", "Hallo" or a cut-off "Kannst", so the list read
as a column of greetings and the one row that mattered was indistinguishable
from the twenty that did not. A title has to name what the conversation was
ABOUT, the way a mail subject does.

Two writers, best first:

* **model** — a connected subscription (or nothing) names finished
  conversations in two to six words. Background work: it never touches a
  per-token API key (``background_policy``), it batches several conversations
  per call, and it backs off when the subscription cannot answer.
* **rules** — :func:`tidy_title`, deterministic and free: it drops greetings,
  small talk, filler sounds, transcription annotations and polite lead-ins
  ("Kannst du bitte mal …"), then keeps the first clause that still says
  something. Every install has it, and it is what a row shows until the model
  has written.

A conversation with no topic at all — only greetings, a test, a fragment —
gets an EMPTY title on purpose. The interface then says what it honestly is
("Voice chat · 09:42") instead of promoting "Was" to a headline.

Nothing here runs on a request path: :meth:`ChatTitler.titles_for` answers from
memory and queues the work for one daemon thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Longest title kept; the sidebar truncates to its real width anyway.
TITLE_MAX_CHARS = 60

#: How many user utterances the rules and the model read per conversation.
USER_TURNS_READ = 6
#: Characters kept per utterance in the model prompt.
UTTERANCE_CHARS = 280
#: Characters of the first assistant reply shown to the model as context.
REPLY_CHARS = 200

#: Conversations named per model call.
MODEL_BATCH = 10
#: Only the newest conversations of a listing are sent to the model; older ones
#: keep their rules title, so a first start does not name a year of history.
MODEL_BACKFILL = 40
#: A typed chat is renamed when it has at least doubled (and grown by this many
#: messages) since its last model title — its topic may have moved on.
MODEL_REGROW_MESSAGES = 4
#: Seconds a typed chat must be quiet before the model names it.
TYPED_SETTLE_S = 45.0
#: Model calls that fail in a row before the titler goes quiet.
MODEL_FAILURES_BEFORE_QUIET = 2
#: How long the titler stays quiet after repeated model failures.
MODEL_QUIET_S = 600.0
#: How long the titler stops asking after finding no subscription at all.
NO_SUBSCRIPTION_QUIET_S = 3 * 3600.0
#: Per-call timeout for the subscription CLI.
MODEL_TIMEOUT_S = 60.0
#: Attempts per conversation before the model gives up on it.
MODEL_ATTEMPTS_PER_ITEM = 2

WRITER_RULES = "rules"
WRITER_MODEL = "model"
WRITER_USER = "user"

KIND_VOICE = "voice"
KIND_TYPED = "agent"


# ----------------------------------------------------------------------
# Rules: tidy_title
# ----------------------------------------------------------------------

#: "[tongue click]", "(laughs)", and Whisper's unterminated "[tongue click".
_ANNOTATION_RE = re.compile(
    r"\[[^\]\n]{0,40}\]|\([^)\n]{0,40}\)|\[[a-z][a-z \-]{0,30}(?=\s|$)|\*[^*\n]{0,30}\*"
)
#: Hesitation sounds anywhere in an utterance.
_FILLER_RE = re.compile(
    r"(?i)(?<!\w)(?:ähm+|äh+|öhm+|öh+|ehm+|hmm*|mhm*|mh+|mm+|uh+m*|um+|erm+)(?!\w)[,.…]*"
)
_GREETING = (
    r"(?:hey|hi|hallo|hello|moin|servus|na|yo|huhu|hej|hola|buenas|"
    r"guten\s+(?:morgen|tag|abend)|good\s+(?:morning|afternoon|evening))"
)
#: A greeting, optionally followed by a name it addresses ("Hey George,").
_GREETING_RE = re.compile(
    rf"(?i:{_GREETING})(?:[\s,]+[A-ZÄÖÜ][\w-]*(?=\s*[,.!?]))?(?![\w-])"
)
#: Whole phrases that carry no topic when they open an utterance.
_SMALL_TALK_RE = re.compile(
    r"(?i)(?:"
    r"was\s+geht(?:\s+ab)?|wie\s+geht(?:'?s|\s+es)(?:\s+dir)?|alles\s+(?:klar|gut|fit)|"
    r"what'?s\s+(?:up|good)(?:\s+up)?|how\s+are\s+you(?:\s+doing)?|how'?s\s+it\s+going|"
    r"qu[eé]\s+tal|c[oó]mo\s+est[aá]s|"
    r"nicht\s+(?:so\s+)?viel|weiß\s+ich\s+(?:nicht|nich)(?:\s+genau)?|"
    r"ich\s+hab(?:e)?\s+(?:ne|eine)\s+frage|i\s+have\s+a\s+question|"
    r"ich\s+verspre\w*(?:\s+mich)?|sorry|entschuldigung|moment|warte\s+mal|warte|"
    r"sehr\s+gut|na\s+gut|ok(?:ay|ey)?|alles\s+klar|also|gut|ja|jo|jep|nee|nein|"
    r"nichts|nix|danke|egal|du|so|und|test(?:ing)?|eins\s+zwei(?:\s+drei)?|"
    r"hörst\s+du\s+mich|can\s+you\s+hear\s+me"
    r")(?![\w-])"
)
#: Polite lead-ins whose remainder IS the request.
_LEAD_IN_RE = re.compile(
    r"(?i)(?:"
    r"(?:kannst|könntest|kannste|würdest|kann(?:st)?)\s+du"
    r"(?:\s+(?:mir|für\s+mich|bitte|mal|eben|kurz|schnell|einfach|nochmal|auch|ganz))*|"
    r"ich\s+(?:möchte|will|würde\s+gerne?|hätte\s+gerne?)(?:\s+(?:bitte|gerne?|jetzt))*"
    r",?\s+dass\s+du(?:\s+(?:mir|für\s+mich|bitte|mal|kurz|einfach))*|"
    r"(?:can|could|would|will)\s+you(?:\s+(?:please|just|quickly|maybe))*|"
    r"(?:puedes|podrías)(?:\s+por\s+favor)?|"
    r"hilf\s+mir(?:\s+(?:bitte|mal|dabei))*|help\s+me|"
    r"please|bitte(?:\s+mal)?|mal|auch|ganz"
    r")(?![\w-])"
)
_LEAD_PUNCT_RE = re.compile(r"^[\s,.;:!?…\-–—'\"]+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…])$")
#: Where a long clause can be cut without losing its head.
_CLAUSE_CUT_RE = re.compile(r"(?i),\s+|\s+(?:und|and|aber|but|weil|because|y|pero)\s+")
_WORD_RE = re.compile(r"[^\W\d_][\w'-]*", re.UNICODE)

#: Words that never make a topic on their own.
_STOP_WORDS = frozenset(
    """
    ich du er sie es wir ihr mir mich dir dich uns euch mein meine dein deine
    der die das den dem des ein eine einen einem einer und oder aber auch noch
    mal bitte ja nein nee doch so was wer wie wo wann warum kannst kann könntest
    hallo hey hi na ok okay gut also dann jetzt hier da ist sind bin bist war
    nicht nichts nix viel einfach eben kurz schnell gerne für mit von zu im in an
    i you he she it we they me my your the a an and or but also just so what who
    how where when why can could would please hello yes no not ok here there is
    are am was be this that of to in on for with
    """.split()
)


def _strip_lead(text: str) -> str:
    """Peel greetings, small talk and polite lead-ins off the front of *text*."""
    previous = None
    while previous != text:
        previous = text
        text = _LEAD_PUNCT_RE.sub("", text)
        for pattern in (_GREETING_RE, _SMALL_TALK_RE, _LEAD_IN_RE):
            match = pattern.match(text)
            if match and match.end() > 0:
                text = text[match.end():]
                break
    return text


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text) if w.lower() not in _STOP_WORDS]


def _substantive(clause: str) -> bool:
    """Does *clause* name something, rather than only greet or hesitate?"""
    words = _content_words(clause)
    if len(words) >= 2:
        return True
    return len(words) == 1 and len(words[0]) >= 5 and len(_WORD_RE.findall(clause)) >= 2


def _dedupe_words(text: str) -> str:
    """'einen einen Agent' -> 'einen Agent' (speech repeats a word while thinking)."""
    out: list[str] = []
    for word in text.split():
        if out and word.lower().strip(",.") == out[-1].lower().strip(",."):
            continue
        out.append(word)
    return " ".join(out)


def _polish(clause: str) -> str:
    """One clause as a title: trimmed at a natural cut, capitalised, no end mark."""
    clause = clause.strip().rstrip(".!?…,;: ").strip()
    words = clause.split()
    if len(words) > 9:
        for match in _CLAUSE_CUT_RE.finditer(clause):
            head = clause[: match.start()]
            if len(head.split()) >= 4:
                clause = head
                break
    clause = clause.strip().rstrip(".!?…,;: ")
    if len(clause) > TITLE_MAX_CHARS:
        # A clause boundary inside the limit reads complete; a word boundary
        # needs the ellipsis to say something was cut.
        heads = [
            clause[: m.start()] for m in _CLAUSE_CUT_RE.finditer(clause)
            if m.start() <= TITLE_MAX_CHARS and len(clause[: m.start()].split()) >= 3
        ]
        if heads:
            clause = heads[-1].rstrip(",;: ")
        else:
            cut = clause[: TITLE_MAX_CHARS - 1]
            if " " in cut:
                cut = cut[: cut.rfind(" ")]
            clause = cut.rstrip(",;: ") + "…"
    return clause[:1].upper() + clause[1:]


def tidy_title(utterances: Iterable[str]) -> str:
    """A topic title from what the user said, or ``""`` when there is none.

    The utterances are joined first: live voice splits one sentence across
    several segments ("Kannst du bitte mal" | "einen Agent spawnen"), and the
    request only reads as one once they are back together.
    """
    joined = " ".join(" ".join(str(u or "").split()) for u in utterances if u)
    text = _FILLER_RE.sub(" ", _ANNOTATION_RE.sub(" ", joined))
    text = _dedupe_words(" ".join(text.split()))
    for _ in range(12):
        text = _strip_lead(text)
        if not text:
            return ""
        parts = _SENTENCE_END_RE.split(text, maxsplit=1)
        clause = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        clause = _strip_lead(clause)
        if _substantive(clause):
            return _polish(clause)
        text = rest
    return ""


# ----------------------------------------------------------------------
# Model: prompt + parse
# ----------------------------------------------------------------------

_SYSTEM = """\
You name conversations for the history list of a personal assistant app, the way \
a mail client shows subjects.

For every numbered conversation, write ONE title:
- 2 to 6 words that name the topic or the task, e.g. "Security audit for the workspace", \
"Set up a trend-analysis agent", "Weather in Berlin".
- In the language the user spoke.
- A noun phrase or a short task. No greeting, no "User asks", no quotes, no emoji, \
no trailing period.
- Spell product and proper names correctly even when the transcript garbled them.
- If the conversation has no topic — only greetings, small talk, a test or a cut-off \
fragment — write a single dash: -

Answer with exactly one line per conversation, in the form
<number>: <title>
and nothing else."""

_ANSWER_RE = re.compile(r"^\s*(\d{1,3})\s*[:.)\]-]\s*(.*?)\s*$")
_TITLE_NOISE_RE = re.compile(r"^[\s\"'„“”«»`*#\-–—]+|[\s\"'„“”«»`*.。!]+$")


@dataclass(frozen=True, slots=True)
class Conversation:
    """What the titler reads of one conversation."""

    user: tuple[str, ...]
    reply: str = ""


def build_prompt(conversations: Sequence[Conversation]) -> str:
    blocks: list[str] = []
    for number, convo in enumerate(conversations, start=1):
        lines = [f"Conversation {number}:"]
        for text in convo.user[:USER_TURNS_READ]:
            lines.append(f"User: {' '.join(text.split())[:UTTERANCE_CHARS]}")
        if convo.reply:
            lines.append(f"Assistant: {' '.join(convo.reply.split())[:REPLY_CHARS]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def clean_model_title(raw: str) -> str:
    """A model's title line, cleaned; ``""`` for its no-topic dash."""
    title = _TITLE_NOISE_RE.sub("", " ".join(str(raw or "").split()))
    if not title or not _WORD_RE.search(title):
        return ""
    if len(title) > TITLE_MAX_CHARS:
        cut = title[: TITLE_MAX_CHARS - 1]
        title = (cut[: cut.rfind(" ")] if " " in cut else cut).rstrip(",;: ") + "…"
    return title


def parse_answer(text: str, count: int) -> dict[int, str]:
    """``{index: title}`` for the numbered lines the model answered (0-based)."""
    out: dict[int, str] = {}
    for line in str(text or "").splitlines():
        match = _ANSWER_RE.match(line)
        if not match:
            continue
        index = int(match.group(1)) - 1
        if 0 <= index < count and index not in out:
            out[index] = clean_model_title(match.group(2))
    return out


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------


@dataclass(slots=True)
class TitleEntry:
    title: str
    writer: str
    #: The listing's change token when the entry was written.
    version: str
    #: Message count the title was written at (typed chats re-title on growth).
    messages_at: int
    #: The stored seed (a typed chat's own title) the entry was written against.
    seed: str = ""
    attempts: int = 0


class TitleBook:
    """Durable titles, keyed by ``(kind, conversation id)``; mirrored in memory."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_titles ("
                " kind TEXT NOT NULL, conv_id TEXT NOT NULL, title TEXT NOT NULL,"
                " writer TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',"
                " messages_at INTEGER NOT NULL DEFAULT 0, seed TEXT NOT NULL DEFAULT '',"
                " attempts INTEGER NOT NULL DEFAULT 0, written_ms INTEGER NOT NULL DEFAULT 0,"
                " PRIMARY KEY (kind, conv_id))"
            )
            self._conn.commit()
            self._mirror: dict[tuple[str, str], TitleEntry] = {
                (row[0], row[1]): TitleEntry(
                    title=row[2], writer=row[3], version=row[4],
                    messages_at=int(row[5]), seed=row[6], attempts=int(row[7]),
                )
                for row in self._conn.execute(
                    "SELECT kind, conv_id, title, writer, version, messages_at, seed, attempts "
                    "FROM chat_titles"
                )
            }

        # Reads take only this lock, never the SQLite one: a listing on the event
        # loop must not wait behind the worker's commits (an fsync each).
        self._mirror_lock = threading.Lock()

    def get(self, kind: str, conv_id: str) -> TitleEntry | None:
        with self._mirror_lock:
            return self._mirror.get((kind, conv_id))

    def put(self, kind: str, conv_id: str, entry: TitleEntry) -> None:
        with self._mirror_lock:
            self._mirror[(kind, conv_id)] = entry
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_titles (kind, conv_id, title, writer, version, messages_at,"
                " seed, attempts, written_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(kind, conv_id) DO UPDATE SET title=excluded.title,"
                " writer=excluded.writer, version=excluded.version,"
                " messages_at=excluded.messages_at, seed=excluded.seed,"
                " attempts=excluded.attempts, written_ms=excluded.written_ms",
                (
                    kind, conv_id, entry.title, entry.writer, entry.version,
                    int(entry.messages_at), entry.seed, int(entry.attempts),
                    int(time.time() * 1000),
                ),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ----------------------------------------------------------------------
# The titler
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TitleRequest:
    """One listed conversation, as the history route sees it."""

    kind: str
    conv_id: str
    #: Changes whenever the conversation does (ended_ms, message count).
    version: str
    message_count: int
    updated_ms: int
    #: Finished (voice) or quiet long enough (typed) for the model to name it.
    settled: bool
    #: The cheap text the row already has (preview / stored title).
    seed: str
    #: Reads the conversation as ``[(role, text), …]`` — called off the request path.
    loader: Callable[[], Sequence[tuple[str, str]]] = field(compare=False)
    #: For typed chats: the title the store derives from a first message, so a
    #: title the user typed themselves is recognised and kept.
    auto_title: Callable[[str], str] | None = field(default=None, compare=False)


def _first_guess(req: TitleRequest) -> str:
    """The title a row shows before its conversation has been read.

    A typed chat's seed may be a title the user typed; only the full read can
    tell, so a seed the rules would blank out is shown as it is meanwhile.
    """
    guess = tidy_title([req.seed])
    if not guess and req.auto_title is not None:
        return req.seed
    return guess


#: Resolves the brain that may write titles, or None. Test seam.
BrainFactory = Callable[[], Any]


def _subscription_brain() -> Any | None:
    """A connected subscription brain, or None — titles never bill an API key."""
    try:
        from jarvis.brain.resolver import resolve_subscription_brain
        from jarvis.core.config import load_config

        return resolve_subscription_brain(load_config(), cli_timeout_s=MODEL_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - no brain means rules titles, said here
        log.info("chat titles: no subscription brain reachable", exc_info=True)
        return None


class ChatTitler:
    """Answers titles from memory; writes better ones on one background thread."""

    def __init__(
        self,
        book: TitleBook,
        *,
        brain_factory: BrainFactory | None = None,
        background: bool = True,
    ) -> None:
        self._book = book
        self._brain_factory = brain_factory or _subscription_brain
        #: False: answer with the rules over each seed and never queue work —
        #: for an app without a data folder, which could not keep a title anyway.
        self._background = background
        self._lock = threading.Condition()
        self._queue: dict[tuple[str, str], tuple[TitleRequest, bool]] = {}
        self._thread: threading.Thread | None = None
        self._model_failures = 0
        self._quiet_until = 0.0
        self._closed = False

    # -- request path ------------------------------------------------------

    def known(self, kind: str, conv_id: str) -> str:
        """The stored title of one conversation; ``""`` when none is known yet."""
        entry = self._book.get(kind, conv_id)
        return entry.title if entry is not None else ""

    def titles_for(self, requests: Sequence[TitleRequest]) -> dict[tuple[str, str], str]:
        """The best title known for each request, right now. Queues what is stale.

        *requests* are expected newest first; only the first
        :data:`MODEL_BACKFILL` of them are offered to the model.
        """
        out: dict[tuple[str, str], str] = {}
        if not self._background:
            return {(r.kind, r.conv_id): _first_guess(r) for r in requests}
        queued = False
        for position, req in enumerate(requests):
            key = (req.kind, req.conv_id)
            entry = self._book.get(*key)
            same_seed = entry is not None and entry.seed == req.seed
            fresh = same_seed and entry is not None and entry.version == req.version
            # A model or user title outlives new messages until it is renewed;
            # a rules title is only as good as the read it came from.
            kept = same_seed and entry is not None and entry.writer in (WRITER_MODEL, WRITER_USER)
            known = entry is not None and (fresh or kept)
            out[key] = entry.title if known and entry is not None else _first_guess(req)
            model = position < MODEL_BACKFILL and self._wants_model(req, entry)
            if not fresh or model:
                queued |= self._enqueue(req, model=model)
        if queued:
            self._ensure_worker()
        return out

    def _wants_model(self, req: TitleRequest, entry: TitleEntry | None) -> bool:
        if not req.settled or time.monotonic() < self._quiet_until:
            return False
        if entry is None or entry.writer == WRITER_RULES:
            return entry is None or entry.attempts < MODEL_ATTEMPTS_PER_ITEM
        if entry.writer == WRITER_USER or entry.seed != req.seed:
            return False
        grown = req.message_count >= max(
            entry.messages_at * 2, entry.messages_at + MODEL_REGROW_MESSAGES
        )
        return req.kind == KIND_TYPED and grown

    def _enqueue(self, req: TitleRequest, *, model: bool) -> bool:
        key = (req.kind, req.conv_id)
        with self._lock:
            if self._closed:
                return False
            pending = self._queue.get(key)
            same = pending is not None and pending[0].version == req.version
            if same and pending is not None and (pending[1] or not model):
                return False
            self._queue[key] = (req, model or bool(pending and pending[1]))
            self._lock.notify()
            return True

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="chat-titler", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._queue.clear()
            self._lock.notify_all()

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._closed:
                    if not self._lock.wait(timeout=30.0):
                        # Idle: let the thread end; the next listing restarts it.
                        if not self._queue:
                            self._thread = None
                            return
                if self._closed:
                    self._thread = None
                    return
                batch = list(self._queue.values())
                self._queue.clear()
            try:
                self.drain(batch)
            except Exception:  # noqa: BLE001 - one bad batch must not end the thread
                log.warning("chat titles: a batch failed", exc_info=True)

    def drain(self, batch: Sequence[tuple[TitleRequest, bool]]) -> None:
        """Rules for every request, then the model for the ones that want it."""
        for_model: list[tuple[TitleRequest, Conversation, TitleEntry]] = []
        for req, model in batch:
            try:
                messages = list(req.loader())
            except Exception:  # noqa: BLE001 - an unreadable row keeps its seed title
                log.debug("chat titles: cannot read %s/%s", req.kind, req.conv_id, exc_info=True)
                # Remember the seed's title for this version, or the row would
                # be re-read on every listing.
                self._book.put(req.kind, req.conv_id, TitleEntry(
                    title=_first_guess(req), writer=WRITER_RULES, version=req.version,
                    messages_at=req.message_count, seed=req.seed,
                    attempts=MODEL_ATTEMPTS_PER_ITEM,
                ))
                continue
            user = tuple(t for r, t in messages if r == "user" and str(t).strip())
            reply = next((t for r, t in messages if r == "assistant" and str(t).strip()), "")
            previous = self._book.get(req.kind, req.conv_id)
            # Any opening message can have named the chat — an agent's message
            # lands with role "agent" and titles an empty chat as well.
            openers = [t for r, t in messages if r in ("user", "agent") and str(t).strip()][:3]
            if req.auto_title is not None and req.seed and openers and \
                    req.seed not in {req.auto_title(t) for t in openers}:
                # The stored title is not the one a first message makes: the
                # user typed it. It wins over every writer here.
                self._book.put(req.kind, req.conv_id, TitleEntry(
                    title=req.seed, writer=WRITER_USER, version=req.version,
                    messages_at=req.message_count, seed=req.seed,
                ))
                continue
            entry = TitleEntry(
                title=tidy_title(user[:USER_TURNS_READ]),
                writer=WRITER_RULES,
                version=req.version,
                messages_at=req.message_count,
                seed=req.seed,
                attempts=previous.attempts if previous is not None else 0,
            )
            renewing = previous is not None and previous.writer == WRITER_MODEL
            if renewing and previous is not None and previous.seed == req.seed:
                # Keep the model's title on screen while a renewal is pending.
                entry = TitleEntry(
                    title=previous.title, writer=WRITER_MODEL, version=req.version,
                    messages_at=previous.messages_at, seed=req.seed,
                )
            self._book.put(req.kind, req.conv_id, entry)
            if model and user and time.monotonic() >= self._quiet_until:
                convo = Conversation(user=user[:USER_TURNS_READ], reply=reply)
                for_model.append((req, convo, entry))
        for start in range(0, len(for_model), MODEL_BATCH):
            if time.monotonic() < self._quiet_until:
                break
            self._name_with_model(for_model[start:start + MODEL_BATCH])

    def _name_with_model(
        self, chunk: Sequence[tuple[TitleRequest, Conversation, TitleEntry]]
    ) -> None:
        brain = self._brain_factory()
        if brain is None:
            # No subscription: rules titles stand. Ask again only after a long
            # pause — a key-only install would otherwise re-read its history
            # every few minutes for a model it will never get.
            self._quiet_until = time.monotonic() + NO_SUBSCRIPTION_QUIET_S
            return
        try:
            answer = asyncio.run(_complete(brain, [c for _, c, _ in chunk]))
        except Exception as exc:  # noqa: BLE001 - the rules title stands; logged
            self._model_failures += 1
            log.info("chat titles: the model could not write (%s)", type(exc).__name__)
            if self._model_failures >= MODEL_FAILURES_BEFORE_QUIET:
                self._quiet_until = time.monotonic() + MODEL_QUIET_S
            for req, _, entry in chunk:
                entry.attempts += 1
                self._book.put(req.kind, req.conv_id, entry)
            return
        self._model_failures = 0
        titles = parse_answer(answer, len(chunk))
        for index, (req, _, entry) in enumerate(chunk):
            if index not in titles:
                entry.attempts += 1
                self._book.put(req.kind, req.conv_id, entry)
                continue
            self._book.put(req.kind, req.conv_id, TitleEntry(
                title=titles[index], writer=WRITER_MODEL, version=req.version,
                messages_at=req.message_count, seed=req.seed,
            ))


async def _complete(brain: Any, conversations: Sequence[Conversation]) -> str:
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=build_prompt(conversations)),),
        system=_SYSTEM,
        temperature=0.1,
        max_tokens=60 + 30 * len(conversations),
        stream=True,
        reasoning_effort="none",
    )
    chunks: list[str] = []

    async def collect() -> None:
        async for delta in brain.complete(request):
            if delta.content:
                chunks.append(delta.content)

    await asyncio.wait_for(collect(), timeout=MODEL_TIMEOUT_S + 15.0)
    return "".join(chunks)


# ----------------------------------------------------------------------
# Process-wide titler
# ----------------------------------------------------------------------

_shared: dict[str, ChatTitler] = {}
_shared_lock = threading.Lock()


def titler_in(folder: str | Path | None) -> ChatTitler:
    """The titler whose book lives in *folder*.

    ``None`` gives a rules-only titler: no durable book, no background thread
    and no model call — nothing worth paying for that could not be kept.
    """
    key = str(Path(folder).resolve()) if folder else ":memory:"
    with _shared_lock:
        titler = _shared.get(key)
        if titler is None:
            if folder:
                titler = ChatTitler(TitleBook(Path(folder) / "chat_titles.db"))
            else:
                titler = ChatTitler(TitleBook(":memory:"), background=False)
            _shared[key] = titler
        return titler


def titler_for_state(state: Any) -> ChatTitler:
    """The titler for a web app's state: its book sits in the configured data folder."""
    config = getattr(state, "config", None)
    if config is None:
        return titler_in(None)
    memory = getattr(config, "memory", None)
    return titler_in(getattr(memory, "data_dir", None) or "./data")


def reset_for_tests() -> None:
    with _shared_lock:
        for titler in _shared.values():
            titler.close()
        _shared.clear()
