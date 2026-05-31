"""BootstrapRunner — First-run interview for the user profile system.

Guides the user through the 5 base questions from BOOTSTRAP.md on very first
contact and persists the answers in USER.md (via UserProfile). After the final
commit, BOOTSTRAP.md is deleted (via `workspace.consume_bootstrap()`).

Design principles:

- **No LLM:** Parsing is deterministic regex + keyword matching. User answers
  flow unfiltered into the profile — no second brain is allowed to sit between
  the user and their own profile.
- **Sync + stateful:** `handle_answer()` is synchronous and returns the next
  question (or the closing greeting) directly. No streaming logic needed.
- **Graceful empty input:** If the user sends "don't know" or an empty answer
  string, we accept defaults and move on — no hard block.
- **German-only:** We ask in German even if the user switches to English later.
  A language switch mid-bootstrap would be complex and error-prone.

How to hook the runner from BrainManager (integration hint)::

    # somewhere in BrainManager.__init__ or the desktop-app backend:
    from jarvis.memory import BootstrapRunner, UserProfile, Workspace

    ws = Workspace.ensure(Path("data/workspace"))
    profile = UserProfile.load(ws.user_path)
    bootstrap = BootstrapRunner(workspace=ws, profile=profile)

    # In the message loop BEFORE the actual brain call:
    async def on_user_message(text: str) -> str:
        if bootstrap.is_pending() and not bootstrap.is_started():
            return bootstrap.start()
        if bootstrap.is_pending():
            reply = bootstrap.handle_answer(text)
            return reply   # next question or closing greeting
        # Bootstrap done → normal brain routing
        return await brain.answer(text, profile=profile)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .user_profile import UserProfile
    from .workspace import Workspace

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Question catalogue (order == flow)
# ----------------------------------------------------------------------
# The questions mirror BOOTSTRAP.md but are worded more concisely for
# voice/chat output — no markdown noise.

_QUESTIONS: tuple[str, ...] = (
    # 0: Name + form of address
    "Wie heisst Du, und wie soll ich Dich ansprechen?",
    # 1: Languages
    "Welche Sprachen sprichst Du mit mir? Default ist Deutsch und Englisch.",
    # 2: Role
    "Was machst Du beruflich? Kurz, ein paar Worte reichen.",
    # 3: Directness
    "Magst Du direkte, kurze Antworten, oder lieber ausfuehrliche Erklaerungen?",
    # 4: Pet peeves
    "Gibt es Sachen, die Dich nerven sollen? Zum Beispiel keine Emojis, keine "
    "Rueckfragen, kein Small-Talk.",
)

_GREETING = (
    "Hey, ich bin Jarvis. Bevor wir richtig starten, frag ich Dich einmal "
    "kurz durch fuenf Basis-Dinge durch — damit ich weiss, mit wem ich's zu "
    "tun habe. "
)

_FAREWELL = (
    "Danke, das reicht fuers Erste. Ich hab alles in USER.md gespeichert und "
    "lerne den Rest im laufenden Betrieb dazu. Du kannst jederzeit direkt in "
    "der Datei editieren."
)


# ----------------------------------------------------------------------
# Parser regexes
# ----------------------------------------------------------------------

# "Ich heisse the maintainer", "ich heiße the maintainer", "mein Name ist the maintainer",
# "ich bin Harald". Case-insensitive. Capture group = name remainder.
_NAME_INTRO_RE = re.compile(
    r"(?:ich\s+heisse|ich\s+heiße|mein\s+name\s+ist|ich\s+bin|my\s+name\s+is|i\s+am|i'm)\s+(.+)",
    re.IGNORECASE,
)

# "Nenn mich the maintainer", "ruf mich X", "call me X" — explicit form-of-address preference
_ADDRESS_RE = re.compile(
    r"(?:nenn(?:e)?\s+mich|ruf(?:e)?\s+mich|call\s+me|sag(?:e)?\s+(?:einfach\s+)?)\s+(.+)",
    re.IGNORECASE,
)

# Strip trailing junk after the actual name (e.g. "the maintainer, aber nenn
# mich gerne Rube" → we want "the maintainer"). Cut at first comma / " aber ".
_NAME_TRAIL_RE = re.compile(r"[,;.!?]|\s+aber\s+|\s+und\s+|\s+but\s+", re.IGNORECASE)

# Language keywords → ISO-639-1. Key match is case-insensitive substring.
_LANGUAGE_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("deutsch", "german", "allemand", " de ", " de,", " de."), "de"),
    (("englisch", "english", "anglais", " en ", " en,", " en."), "en"),
    (("spanisch", "spanish", "espanol", "español", " es ", " es,"), "es"),
    (("franzoesisch", "französisch", "french", "francais", "français", " fr "), "fr"),
    (("italienisch", "italian", "italiano", " it ", " it,"), "it"),
    (("niederlaendisch", "niederländisch", "dutch", "nederlands", " nl "), "nl"),
    (("portugiesisch", "portuguese", " pt ", " pt,"), "pt"),
    (("polnisch", "polish", "polski", " pl "), "pl"),
    (("tuerkisch", "türkisch", "turkish", "türkçe", " tr "), "tr"),
)

# Directness signals — keyword lists. First match wins.
_DIRECT_KEYWORDS = (
    "direkt", "kurz", "knapp", "tldr", "no fluff", "auf den punkt",
    "to the point", "brief", "short", "concise",
)
_VERBOSE_KEYWORDS = (
    "ausfuehrlich", "ausführlich", "detailliert", "erklaer", "erklär",
    "verbose", "deep dive", "deep-dive", "tief", "genau", "mit details",
)

# Pet-peeves splitter: ",", " und ", " sowie ", " plus ", " & "
_PEEVE_SPLIT_RE = re.compile(r"\s*(?:,|;|\bund\b|\bsowie\b|\bplus\b|\band\b|&)\s*", re.IGNORECASE)

# Extract "Emojis" from "Keine Emojis". Analogous to English "no X".
_NEGATIVE_PREFIX_RE = re.compile(
    r"^\s*(?:keine?|kein|no|nicht|never|please\s+no|bitte\s+kein(?:e)?)\s+",
    re.IGNORECASE,
)

# "Weiss nicht" / "egal" — user skips the question
_SKIP_KEYWORDS = (
    "weiss nicht", "weiß nicht", "keine ahnung", "egal", "ist mir egal",
    "dont know", "don't know", "pass", "skip", "ueberspring",
    "überspring", "weiter",
)


# ----------------------------------------------------------------------
# BootstrapRunner
# ----------------------------------------------------------------------

@dataclass
class BootstrapRunner:
    """State machine for the first-run interview.

    Stages (`_step_idx`):
        0 → Name + form of address  (question 1)
        1 → Languages               (question 2)
        2 → Role                    (question 3)
        3 → Directness              (question 4)
        4 → Pet peeves              (question 5)
        5 → finished                (runner is done)

    Only `start()` and `handle_answer()` are public API points; all
    `_parse_*` helpers are internal.
    """

    workspace: Workspace
    profile: UserProfile
    _step_idx: int = 0
    _started: bool = False
    _finished: bool = False
    # Buffer for answers — for debug/logging. Not persisted.
    _answers: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_pending(self) -> bool:
        """True as long as the bootstrap has not yet been completed.

        Uses `Workspace.is_bootstrap_needed()` as the source of truth — if
        BOOTSTRAP.md is gone, the runner is done regardless of runtime state.
        """
        if self._finished:
            return False
        return self.workspace.is_bootstrap_needed()

    def is_started(self) -> bool:
        """True once `start()` has been called."""
        return self._started

    def is_finished(self) -> bool:
        """True once all 5 questions have been answered and persisted."""
        return self._finished

    @property
    def current_question(self) -> str | None:
        """Which question is next? None when finished."""
        if self._finished or self._step_idx >= len(_QUESTIONS):
            return None
        return _QUESTIONS[self._step_idx]

    def start(self) -> str:
        """Starts the interview and returns the greeting + first question.

        Idempotent: if `start()` was already called, it simply returns the
        current question — the caller sees no duplicate greeting.
        """
        if self._finished:
            return _FAREWELL
        if not self._started:
            self._started = True
            log.info("Bootstrap-Interview gestartet (Workspace=%s)",
                     self.workspace.root)
            return _GREETING + (_QUESTIONS[0] if _QUESTIONS else "")
        # Already started → just re-deliver the current question
        q = self.current_question
        return q if q is not None else _FAREWELL

    def handle_answer(self, user_text: str) -> str:
        """Processes a user answer and returns the next question.

        On the last question: persists the profile, deletes BOOTSTRAP.md,
        and returns the closing greeting.
        """
        if self._finished:
            return _FAREWELL
        if not self._started:
            # Safety net: if the caller forgot to call start(), we silently do
            # it here — the first answer is still parsed correctly.
            self._started = True

        text = (user_text or "").strip()
        self._answers.append(text)

        # Dispatch to stage parser
        step = self._step_idx
        try:
            if step == 0:
                self._parse_name_and_address(text)
            elif step == 1:
                self._parse_languages(text)
            elif step == 2:
                self._parse_role(text)
            elif step == 3:
                self._parse_directness(text)
            elif step == 4:
                self._parse_pet_peeves(text)
            else:
                log.warning("handle_answer() called even though step_idx=%d is out of range", step)
        except Exception:
            # Parser errors must not stop the flow — we log and continue to
            # the next question. Default values are already set (parsers are
            # defensive).
            log.exception("Error parsing bootstrap answer (step=%d)", step)

        self._step_idx += 1

        # Last question answered? → commit + finalize
        if self._step_idx >= len(_QUESTIONS):
            self._finalize()
            return _FAREWELL

        return _QUESTIONS[self._step_idx]

    # ------------------------------------------------------------------
    # Stage parsers (one per question)
    # ------------------------------------------------------------------

    def _parse_name_and_address(self, text: str) -> None:
        """Question 1: extract name + form of address.

        Strategy:
        1. Search for "nenn mich X" — if found, X = preferred_address.
        2. Search for "ich heisse/ich bin/mein Name ist X" — X = name.
        3. Fallback: take the whole text as the name (trimmed).
        """
        if self._is_skip(text):
            log.info("Bootstrap-Name uebersprungen — setze auf 'User'")
            self.profile.set("identity", "name", "User")
            return

        # Default: full text as name
        name_candidate = text

        # 1) "Ich heisse X" / "ich bin X" / "mein Name ist X"  (name intro patterns)
        m = _NAME_INTRO_RE.search(text)
        if m:
            name_candidate = m.group(1).strip()

        # 2) Optional: "Nenn mich Y" → separate preferred_address
        address_candidate: str | None = None
        ma = _ADDRESS_RE.search(text)
        if ma:
            address_candidate = ma.group(1).strip()
            # If _NAME_INTRO_RE found nothing but the user wrote
            # "Ich bin the maintainer, nenn mich Rube": the address group can overlap the
            # name — we keep both.

        # Remove trailing junk (comma, "aber ...", "und ...")
        name = _trim_name(name_candidate)
        address = _trim_name(address_candidate) if address_candidate else None

        if not name:
            name = "User"

        self.profile.set("identity", "name", name)
        # If the user explicitly said "nenn mich X" → preferred_address != name
        # Otherwise: preferred_address stays == name (via property fallback)
        if address and address.lower() != name.lower():
            self.profile.set("identity", "preferred_address", address)
        else:
            self.profile.set("identity", "preferred_address", name)

        log.info("Bootstrap-Name: name=%r address=%r", name, address or name)

    def _parse_languages(self, text: str) -> None:
        """Question 2: languages. Default [de, en] if nothing detected."""
        # Empty or skip → keep default
        if not text or self._is_skip(text):
            langs = ["de", "en"]
        else:
            langs = _detect_languages(text)
            if not langs:
                langs = ["de", "en"]

        for code in langs:
            self.profile.append_list("identity", "languages", code)
        self.profile.set("identity", "primary_language", langs[0])
        log.info("Bootstrap-Languages: %s (primary=%s)", langs, langs[0])

    def _parse_role(self, text: str) -> None:
        """Question 3: role. Stored as a free observation — there is no dedicated role field."""
        if not text or self._is_skip(text):
            log.info("Bootstrap-Rolle uebersprungen")
            return
        # Role goes into the observations section — Curator can structure it later.
        self.profile.append_observation(
            "identity.role",
            _truncate(text, 200),
            evidence=text,
        )
        log.info("Bootstrap-Rolle notiert: %r", _truncate(text, 80))

    def _parse_directness(self, text: str) -> None:
        """Question 4: directness / verbosity.

        Default (on skip or mixed signal): directness=4, verbosity=normal.
        """
        lo = text.lower() if text else ""
        is_direct = any(k in lo for k in _DIRECT_KEYWORDS)
        is_verbose = any(k in lo for k in _VERBOSE_KEYWORDS)

        if is_direct and not is_verbose:
            directness, verbosity = 5, "tldr"
        elif is_verbose and not is_direct:
            directness, verbosity = 3, "deep-dive"
        else:
            # Mixed or no signal → safe midpoint
            directness, verbosity = 4, "normal"

        self.profile.set("communication", "directness", directness)
        self.profile.set("communication", "verbosity", verbosity)
        log.info("Bootstrap-Directness: directness=%d verbosity=%s",
                 directness, verbosity)

    def _parse_pet_peeves(self, text: str) -> None:
        """Question 5: pet peeves. Split + optionally remove "keine X" prefix."""
        if not text or self._is_skip(text):
            log.info("Bootstrap-Pet-Peeves uebersprungen")
            return

        peeves = _extract_peeves(text)
        for peeve in peeves:
            self.profile.append_list("values", "pet_peeves", peeve)

        # Special case: "keine Emojis" also sets the structured field directly
        lowered = text.lower()
        if "emoji" in lowered and _has_negation_near(lowered, "emoji"):
            self.profile.set("communication", "emoji_ok", False)

        log.info("Bootstrap-Pet-Peeves: %s", peeves)

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    def _finalize(self) -> None:
        """Persists the profile and deletes BOOTSTRAP.md."""
        try:
            self.profile.save()
            log.info("Bootstrap: USER.md gespeichert nach %s", self.profile.path)
        except Exception:
            log.exception("Bootstrap: USER.md konnte nicht gespeichert werden")
            # Still mark as finished — no re-run is better than an infinite loop.

        try:
            self.workspace.consume_bootstrap()
            log.info("Bootstrap: BOOTSTRAP.md geloescht")
        except Exception:
            log.exception("Bootstrap: BOOTSTRAP.md konnte nicht geloescht werden")

        self._finished = True

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _is_skip(text: str) -> bool:
        if not text:
            return True
        lo = text.lower().strip()
        return any(kw in lo for kw in _SKIP_KEYWORDS)


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _trim_name(raw: str | None) -> str:
    """Strips trailing junk after the name and capitalizes it.

    Examples:
        "the maintainer, aber nenn mich rube" → "the maintainer"
        "the maintainer"                → "the maintainer"
        "  harald  "                 → "Harald"
    """
    if not raw:
        return ""
    # Split at first junk marker
    parts = _NAME_TRAIL_RE.split(raw, maxsplit=1)
    candidate = (parts[0] if parts else raw).strip().strip('"\'')
    if not candidate:
        return ""
    # Title-case at word level (not .title() — that breaks e.g. "McKay")
    words = [_capitalize_first(w) for w in candidate.split()]
    return " ".join(words)


def _capitalize_first(word: str) -> str:
    """Capitalize only the first character, leaving the rest unchanged."""
    if not word:
        return word
    return word[0].upper() + word[1:]


def _detect_languages(text: str) -> list[str]:
    """Finds ISO-639-1 codes by keyword matching. Deduplicated, order-preserving."""
    # Normalise the search window: pad with whitespace so that " de " also
    # matches at sentence start/end.
    haystack = " " + text.lower().replace(",", " , ").replace(".", " . ") + " "
    found: list[str] = []
    for keywords, code in _LANGUAGE_MAP:
        for kw in keywords:
            if kw in haystack and code not in found:
                found.append(code)
                break
    return found


def _extract_peeves(text: str) -> list[str]:
    """Splits a pet-peeves text into clean fragments.

    "keine Emojis, keine Confirmation-Fragen und kein Small-Talk" →
    ["Emojis", "Confirmation-Fragen", "Small-Talk"]
    """
    fragments = _PEEVE_SPLIT_RE.split(text)
    peeves: list[str] = []
    for frag in fragments:
        cleaned = _clean_peeve(frag)
        if cleaned:
            peeves.append(cleaned)
    # Dedup (case-insensitive), order-preserving
    seen: set[str] = set()
    dedup: list[str] = []
    for p in peeves:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(p)
    return dedup


def _clean_peeve(fragment: str) -> str:
    """Normalises a single pet-peeve fragment.

    - "keine Emojis" → "Emojis"
    - "no fluff"     → "fluff"
    - "  und dann "  → "" (empty, dropped)
    """
    s = fragment.strip().strip('"\'.,;:!?')
    if not s:
        return ""
    # "keine X" → "X"
    s = _NEGATIVE_PREFIX_RE.sub("", s).strip()
    # Drop overly generic fragments
    if s.lower() in {"", "nichts", "nix", "keine", "kein", "nothing", "none"}:
        return ""
    return s


def _has_negation_near(text: str, keyword: str) -> bool:
    """True if a negation word appears before `keyword` (within ~15 characters)."""
    idx = text.find(keyword)
    if idx == -1:
        return False
    window_start = max(0, idx - 15)
    window = text[window_start:idx]
    return bool(re.search(r"\b(kein(?:e)?|no|not|nicht|ohne|without)\b", window, re.IGNORECASE))


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


# ----------------------------------------------------------------------
# Smoke test (python -m jarvis.memory.bootstrap_runner)
# ----------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import shutil
    import sys
    import tempfile

    # Windows stdout defaults to cp1252 — we need UTF-8 for umlauts.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # Late imports — so the module can be imported without the package installed
    # (development convenience).
    from jarvis.memory.user_profile import UserProfile
    from jarvis.memory.workspace import Workspace

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_bootstrap_smoke_"))
    print(f"[smoke] tmp workspace: {tmpdir}")
    try:
        ws = Workspace.ensure(tmpdir)
        assert ws.is_bootstrap_needed(), "Fresh workspace should require bootstrap"

        profile = UserProfile.load(ws.user_path)
        runner = BootstrapRunner(workspace=ws, profile=profile)

        assert runner.is_pending(), "Runner should be pending"
        assert not runner.is_started()
        assert not runner.is_finished()

        # --- Stage 0: greeting + Q1 ---
        greeting = runner.start()
        print(f"\n[Q1] {greeting}\n")
        assert runner.is_started()
        assert "Jarvis" in greeting
        assert _QUESTIONS[0] in greeting

        # --- Stage 0 → 1: name ---
        r = runner.handle_answer("Ich heisse the maintainer, aber nenn mich Rube")
        print(f"[A1] the maintainer -> next: {r}")
        assert profile.get("identity", "name") == "the maintainer", profile.get("identity", "name")
        assert profile.get("identity", "preferred_address") == "Rube"
        assert r == _QUESTIONS[1]

        # --- Stage 1 → 2: languages ---
        r = runner.handle_answer("Deutsch und English, manchmal Spanisch")
        print(f"[A2] Sprachen -> next: {r}")
        langs = profile.get("identity", "languages") or []
        assert "de" in langs and "en" in langs and "es" in langs, langs
        assert profile.get("identity", "primary_language") == "de"
        assert r == _QUESTIONS[2]

        # --- Stage 2 → 3: role ---
        r = runner.handle_answer("Ich baue Personal Jarvis, Solo-Developer")
        print(f"[A3] Rolle -> next: {r}")
        assert r == _QUESTIONS[3]

        # --- Stage 3 → 4: directness ---
        r = runner.handle_answer("Direkt und kurz bitte, no fluff")
        print(f"[A4] Direkt -> next: {r}")
        assert profile.get("communication", "directness") == 5
        assert profile.get("communication", "verbosity") == "tldr"
        assert r == _QUESTIONS[4]

        # --- Stage 4 → finished ---  (pet peeves)
        r = runner.handle_answer("keine Emojis, keine Confirmation-Fragen und kein Small-Talk")
        print(f"[A5] Peeves -> final: {r}")
        peeves = profile.get("values", "pet_peeves") or []
        assert "Emojis" in peeves, peeves
        assert any("Confirmation" in p for p in peeves), peeves
        assert any("Small-Talk" in p for p in peeves), peeves
        assert profile.get("communication", "emoji_ok") is False

        assert runner.is_finished(), "Runner should be finished"
        assert not runner.is_pending(), "Runner should no longer be pending"
        assert not ws.bootstrap_path.exists(), "BOOTSTRAP.md should have been deleted"
        assert ws.user_path.exists() and ws.user_path.read_text(encoding="utf-8").strip()

        # Reload from disk → round-trip is consistent
        reloaded = UserProfile.load(ws.user_path)
        assert reloaded.get("identity", "name") == "the maintainer"
        assert reloaded.get("identity", "preferred_address") == "Rube"
        assert reloaded.get("communication", "directness") == 5

        print("\n[smoke] ALL OK")
        print(f"[smoke] USER.md snippet:\n---\n{ws.user_path.read_text(encoding='utf-8')[:600]}\n---")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    sys.exit(0)
