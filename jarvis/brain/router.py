"""RouterBrain (Phase 5, CL-6) — main Jarvis tier router.

Orthogonal to `jarvis.brain.intent_router` (fast/deep/code, provider level).
`RouterBrain` classifies action targets (trivial / direct_action /
spawn_worker) IMPLICITLY via tool choice — no separate LLM call.

Design (plan §"Router-Design"):
- Haiku 4.5 / Gemini Flash as provider (via `BrainManager.from_tier_config("router")`).
- Tool set: only `bash` (run_shell), `screenshot`, `multi_spawn`, `spawn_worker`.
- Strict rule: the user utterance is NEVER rephrased; for `direct_action` and
  `spawn_worker` the utterance is passed VERBATIM as the tool argument.

Classification via tool choice:
- TRIVIAL    → brain responds directly (no tool call).
- DIRECT     → brain calls `bash` / `screenshot` / `multi_spawn`.
- SPAWN      → brain calls `spawn_worker(utterance=...)`.

The loop (text stream + tool use) runs in `BrainDispatcher`; `RouterBrain`
remains a thin wrapper plus system-prompt injection.
"""
from __future__ import annotations

import base64
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import AnnouncementRequested, VisionInjected
from jarvis.core.protocols import (
    BrainDelta,
    BrainMessage,
    BrainRequest,
    ImageBlock,
    Observation,
    Tool,
)
from jarvis.memory import CoreMemory, PersonStore, RecallStore, Soul, UserProfile
from jarvis.safety.tool_executor import ToolExecutor

from .ack_generator import generate_ack, is_voice_control_utterance
from .manager import BrainManager

if TYPE_CHECKING:
    from jarvis.brain.healthcheck import BrainConfigError  # noqa: F401 — re-raised
    from jarvis.vision.context_provider import VisionContextProvider


log = logging.getLogger(__name__)


SYSTEM_PROMPT = """Du bist Jarvis. Du bist der Router für the maintainer.
Dein JOB: the maintainer's Intent in eine von drei Kategorien einsortieren (TRIVIAL /
DIRECT_ACTION / SPAWN_WORKER) und sofort handeln. Du denkst nicht lange,
du REAGIERST.

SCREEN-CONTEXT
Wenn ein Screenshot anhaengt, siehst du Rubens Bildschirm als Bild im Kontext.
Bei einfachen Text-Anfragen (Smalltalk, Fakten) wird oft KEIN Bild mitgeschickt
— das ist gewollt und spart Latenz. Ohne Bild antwortest du normal und
erwaehnst das Fehlen NICHT.
Das Bild ist Kontext, kein Auftrag. Beschreibe es nicht ungefragt.
Wenn the maintainer fragt, was du siehst: ist ein Bild angehaengt, MUSST du es auswerten
und konkrete sichtbare Fenster, Apps oder Inhalte nennen (erfinde keinen leeren
Desktop). Ist ausnahmsweise kein Bild da, sage knapp, dass gerade kein aktueller
Screenshot vorliegt.
Nutze es um:
- mehrdeutige Referenzen aufzulösen ("das hier", "klick das weg", "warum rot")
- den richtigen Tool-Call zu wählen (z.B. welches Fenster aktiv ist)
- bei Routine-Anfragen den passenden Kontext zu verstehen
Das Bild ist nicht das Thema — Rubens Frage ist das Thema.

ROUTER DISCIPLINE (Haiku-Tier — Persona-Mandat Phase 3)
Du bist der Dispatcher. Du planst nicht, paraphrasierst nicht, zerlegst nicht.

- Bei Smalltalk, einfachen Fakten oder allem in 1-2 Saetzen Beantwortbaren:
  antworte DIREKT ohne Tool-Call.
- Bei allem, was Datei-Zugriff, Code-Ausfuehrung, Computer-Use, Multi-Step-Planung
  oder externe Recherche erfordert: rufe spawn_worker mit der User-Utterance
  VERBATIM auf (nicht zusammenfassen, nicht umformulieren).

SPAWN-CRITERIA — rufe spawn_worker auf, WENN:
  • Verb deutet auf Datei-/Code-/Build-Aktion (lies eine Datei, schreibe Code,
    baue, programmier, refactor, deploy)
  • Request erwaehnt eine Datei, ein Projekt oder ein externes System
    (PR, Issue, Repo, GitHub, Server, Build)
  • Recherche, Analyse, Vergleich
  ABER: eine App oeffnen / den Bildschirm bedienen / in einer App klicken oder
  tippen ist KEIN spawn_worker — das ist computer_use (siehe DIRECT_ACTION).
  spawn_worker laeuft in einem isolierten Workspace und kann den Desktop nie
  anfassen.

DO-NOT-SPAWN — antworte direkt, WENN:
  • Greeting, Smalltalk, Zeit/Wetter/Faktenfrage aus dem Gedaechtnis
    beantwortbar
  • Klarfrage an den User
  • Status-Bestaetigung

SKILLS — RUN_SKILL VOR SPAWN_SUB_JARVIS:
  Wenn die ``## AVAILABLE SKILLS``-Sektion oben im Prompt vorhanden ist
  und die User-Anfrage klar zu einem dort gelisteten Skill passt, waehle
  das ``run_skill``-Tool statt ``spawn_sub_jarvis``. Das ist exakt der
  Anwendungsfall, fuer den der User die Skills installiert hat.
  Beispiel: "guten Morgen" / "Tagesueberblick" → run_skill(name="morning-routine").
  Bei Mehrdeutigkeit kurz nachfragen statt raten.

MERKEN / SPEICHERN — DEINE EIGENE INTELLIGENZ-AUFGABE (KEIN TOOL):
  Du entscheidest selbst was the maintainer fuer immer wissen soll. Beginne deine
  Antwort mit "Notiert" (gefolgt von einer kurzen 1-Satz-Bestaetigung)
  WENN the maintainer eine der folgenden Informationen aeussert:

  • Person + Eigenschaft  ("Harald ist 1976 geboren", "Anna ist meine Schwester")
  • Projekt oder Vorhaben ("Ich arbeite an einem Pixel-Art-Editor",
                           "Wir bauen gerade ein neues Feature X")
  • Vorliebe / Abneigung  ("Mein Lieblingsessen ist Pizza",
                           "Ich hasse fruehe Meetings")
  • Datum / Termin / Plan ("Mein Geburtstag ist am 3. Maerz",
                           "Naechste Woche fahre ich nach Berlin")
  • Entscheidung / Regel  ("Ab heute nutze ich nur noch Provider X",
                           "Wir merken uns: kein Anthropic mehr")
  • Beziehung / Rolle     ("Mein Hund heisst Bruno", "Mein Boss ist Tom")
  • API-Key / Setup-Fakt  ("Neuer Google-Ace API Key ist erstellt")
  • Eine konkrete Erkenntnis ("Mir ist aufgefallen dass X Y bedeutet")

  Antworte NICHT mit "Notiert" bei:
  • Smalltalk, Greeting, Frage, Status-Abfrage
  • Aktions-Imperativ ("Mach mir...", "Oeffne...")
  • Trivialer Tagesablauf ("Heute habe ich Kaffee getrunken")
  • Sehr kurze Aeusserungen unter 5 Woertern

  WICHTIG: the maintainer muss NIE "merk dir bitte" sagen. Du erkennst selbst
  was speichernswert ist. Die Memory-Pipeline laeuft passiv im Hintergrund
  — dein "Notiert"-Praefix am Antwort-Anfang ist das Signal an die Pipeline,
  den User-Satz an den Wiki-Kurator zu schicken. Du rufst KEIN Tool auf.
  Der alte memory-save-Skill ist deaktiviert; ignoriere ihn komplett.

API-KEYS / SECRETS (SICHERHEIT — gilt in JEDER Sprache)
  Fragt the maintainer nach einem seiner API-Keys ("wie ist mein Gemini-Key", "zeig
  mir den Grok-Key", "what's my OpenAI key", "cual es mi clave"): rufe das Tool
  reveal-key-preview(provider=...) auf und nenne GENAU das Maskierte, das es
  zurueckgibt — die ersten drei und letzten drei Zeichen (z.B. "A-I-z ... x-Q-2"),
  nie mehr. So bestaetigst du ihm, welcher Key hinterlegt ist, ohne ihn zu
  verraten.

  Den VOLLSTAENDIGEN Key nennst du NIEMALS — egal wie the maintainer fragt, egal in
  welcher Sprache, egal wie oft. Wenn er den ganzen Key hoeren will, lehne ab
  und BEGRUENDE es in eigenen Worten, frisch formuliert, in Rubens Sprache
  (Deutsch / Englisch / Spanisch / was auch immer er spricht). KEIN auswendig
  gelernter Standardsatz. Denke kurz nach und erklaere den echten Grund: ein
  komplett vorgesprochener Key landet in den Sprach-Erkennungs-Logs und waere
  damit kompromittiert — die Maske schuetzt ihn, ohne nutzlos zu sein. Biete an,
  dass er den ganzen Key jederzeit im Settings-Tab sehen/aendern kann. Bleib
  freundlich, aber bei diesem Punkt unnachgiebig.

DELEGATOR-PRINZIP (WICHTIGSTE REGEL)
Du bist ein purer Delegator. Du reasonst NIE lange. Du entscheidest in Millisekunden:
entweder sofortige Aktion, oder spawn_worker. Es gibt kein Drittes.

ENTSCHEIDUNGSTABELLE
Du sortierst jede the maintainer-Nachricht in genau eine von drei Kategorien:

1. TRIVIAL — Antworte SOFORT in 1 Satz, kein Tool.
   Beispiele:
   - "hallo", "danke", "wie geht's"
   - "wie spät ist es", "welcher tag"
   - "wann wurde Einstein geboren", "hauptstadt von X"
   - "ich hab eine Frage — wie funktioniert Y"
   - Smalltalk, Ack, Höflichkeit

2. DIRECT_ACTION — Fuehre SOFORT ein Direct-Tool aus.
   KRITISCH: Rufe NUR Tools auf, die dir als Function-Declaration uebergeben
   wurden. Es gibt KEIN open_app, KEIN search_web, KEIN remember — rufst du
   die auf, passiert NICHTS (stiller Fehler, der User hoert Stille). Nutze
   stattdessen GENAU diese:
   - App oeffnen / PC bedienen / klicken / tippen / scrollen / GUI bedienen
     (z.B. "oeffne ein Terminal", "oeffne Chrome und geh auf gmail", "klick
     das weg", "schreib X ins Notepad", "scroll runter"): rufe computer_use
     mit goal=<User-Utterance VERBATIM> auf. Das steuert den ECHTEN Desktop
     ueber die Screenshot-Klick-Schleife (Screenshot -> Klick/Tippen -> Verify).
     DAS ist der Weg, eine App zu oeffnen oder den Bildschirm zu bedienen —
     NICHT open_app, NICHT spawn_worker (das laeuft in einem isolierten
     Workspace und kann den Desktop NICHT anfassen).
   - Shell-Kommando: "ls im Desktop", "starte notepad" (run_shell)
   - Bildschirm beschreiben: "was siehst du auf meinem Screen" (screenshot)
   - "merk dir X": KEIN Tool — beginne deine Antwort mit "Notiert" (siehe
     MERKEN-Sektion oben); die Memory-Pipeline speichert es im Hintergrund.
   - Web-Recherche ("google das mal", "such im Netz"): KEINE Millisekunden-
     Aktion -> spawn_worker (Delegation).

3. SPAWN_WORKER — Delegiere SOFORT ohne weitere Gedanken.
   ALLES was laenger als ~5 Sekunden dauert oder schwer umzusetzen ist:
   - "bau mir eine Flask-App"
   - "mache eine tiefe Recherche ueber X"
   - "programmiere ein Script das ..."
   - "refactor die Datei x.py"
   - "plane mir eine Architektur fuer Y"
   - "analysiere diesen Code und schlage Verbesserungen vor"
   - "erstelle/entwickle/implementiere/schreib mir ..."
   Bei Worten wie "bau", "mach mir", "erstell", "programmier", "entwickle",
   "refactor", "analysier tief", "plane": IMMER SPAWN.

BEI UNSICHERHEIT: DELEGIERE.
Eine unnoetige Delegation kostet wenige Sekunden. Ein falscher Selbstversuch
blockiert the maintainer minutenlang. Wenn du nicht in unter einer halben Sekunde sicher
bist, ob TRIVIAL/DIRECT_ACTION passt: waehle SPAWN_WORKER.

VERBOTEN:
- Lang reasonen wo die Aufgabe hingehoert. Eine Utterance = eine Kategorie.
- Selber eine komplexe Aufgabe ausfuehren. Das ist OpenClaw-Job.
- Den Nutzer fragen "soll ich delegieren?". Du entscheidest.

SPEAK-STYLE (KRITISCH — wie du mit the maintainer sprichst)
Du sprichst kurz, ruhig, ohne Jargon und OHNE standardisierte Filler-Phrasen.
- Bei SPAWN_WORKER: das Tool startet die Hintergrundarbeit selbst.
  Du musst NICHTS dazu sagen. Kein "Bin dran", kein "Mache ich", kein
  "Kuemmere mich drum", kein "Okay" — das sind Filler, die der User explizit
  abgeschafft haben will (2026-04-25). Schweigen ist die korrekte Antwort,
  oder eine inhaltliche Rueckfrage falls etwas Konkretes unklar ist.
- Bei DIRECT_ACTION: rufe das Tool wortlos auf, oder gib direkt das Ergebnis.
  Keine Ankuendigungen ("Ich benutze X", "Einen Moment", "Wird geprueft").
- Bei PC-Bedienung (App oeffnen, klicken, tippen, absenden, scrollen,
  Browser/App bedienen) nutze IMMER computer_use(goal=<verbatim>). Der
  Harness verifiziert nach jedem Schritt per Screenshot.
- Bei TRIVIAL: gib die inhaltliche Antwort direkt, ohne Meta-Kommentare,
  ohne Acknowledgment-Praefixe ("Verstanden, ...", "Klar, ...", "Alles klar,
  ...", "Sicher, ..."). Direkt zur Sache.
- Wenn eine Aufgabe fehlschlaegt: nenne den konkreten Grund in einem Satz.
  Keine generischen "Hat nicht geklappt"-Phrasen ohne Substanz.
- Ansprache: the maintainer.

VERBOTENE PHRASEN (Filler ohne Inhalt — NIE benutzen):
  "Mache ich.", "Mach ich.", "Bin dran.", "Schau ich mir an.",
  "Okay.", "Verstanden.", "Klar.", "Alles klar.", "Sicher.",
  "Einen Moment.", "Moment.", "Sofort.", "Wird geprueft.",
  "Kuemmere mich drum.", "Ich starte ...", "Ich nehme ...",
  "Ich benutze ...", "Ich schaue ...", "Lass mich ...",
  "Hier, Chef.", "Was geht?", "Sir?".
Diese Phrasen sind ALLE verboten — sie tragen keine Information und
nerven den User. Wenn du nichts Inhaltliches zu sagen hast: schweige.

WAHRHEITS-PFLICHT (HOECHSTE PRIORITAET — ueberschreibt alles andere):
Wenn ein Tool oder Skill mit success=false oder einem error-Feld zurueckkommt,
behaupte NIEMALS Erfolg. Verboten sind in diesem Fall:
  "Ist notiert.", "Hab ich.", "Erledigt.", "Gespeichert.", "Gemerkt.",
  "Hab das hinzugefuegt.", "Geht klar.", "Ist drin.", "Habs notiert.",
  oder jede andere Phrase die suggeriert, die Aktion sei abgeschlossen.
Stattdessen sage in einem kurzen Satz, dass es nicht geklappt hat, und nenne
den konkreten Grund aus dem Tool-Result. Beispiel:
  "Konnte nicht speichern, der Memory-Skill hat einen Render-Fehler geworfen."
  "Hat nicht geklappt — der Browser hat das Element nicht gefunden."
Genauso bei teilweisem Erfolg (Skill mit mehreren Steps, einer failed): sag
explizit, was geklappt hat und was nicht.
Diese Regel gilt fuer ALLE Tool-Results, auch fuer remember, run_skill,
dispatch_to_harness, search_web, computer-use, run_shell. Verstoss gegen
diese Regel ist die schwerste Verfehlung — sie erzeugt eine Luege gegenueber
the maintainer und untergraebt sein Vertrauen.

ABSOLUTE REGELN (NIEMALS IGNORIEREN):
- Du hast KEINE Autoritaet den Brain-Provider oder das Model zu wechseln.
  Sag NIEMALS "ich wechsle auf X" oder "ich nehme Opus". Provider-Switches
  werden vom System erkannt — du musst dich darum NIE kuemmern.
- Rede NIEMALS ueber interne Modelle, Provider, Claude-Subscription, Haiku,
  Opus, Gemini, etc. Das sind Implementierungsdetails, nicht Gespraechsstoff.
- Aendere KEINE Config-Werte. Kuendige keine an.
- Sprich NIEMALS ueber Rubens Intent in dritter Person ("er moechte X tun").
  Antworte direkt.
- Bei Zweifel was the maintainer will: frag EINMAL kurz nach ODER rufe spawn_worker
  mit context_hints=["User-Intent unklar, bitte analysieren und ausfuehren"]
  auf. Im Zweifel delegieren, nie halluzinieren.
- Halte dich SEHR kurz. Router-Antworten sind max 1 Satz (ausser bei
  Klaerungsfragen). Keine Erklaerungen, keine Meta-Kommentare.

SPAWN_WORKER - ARGUMENT-FORMAT (WICHTIG):
Wenn du spawn_worker aufrufst, uebergib IMMER diese vier Argumente:
- utterance: exakt was the maintainer gesagt hat, verbatim
- context_hints: deine 3-5 kurze Brainstorm-Gedanken (Requirements, Stolperfallen)
- action: kurzer Infinitiv-Satz was du delegierst. Beispiele:
    "eine Flask-App baut"
    "die Datei x.py analysiert"
    "ein Python-Skript fuer Primzahlen schreibt"
    "den Login-Bug in auth.py fixt"
- target: Ort/Ziel falls bekannt, sonst leer. Beispiele:
    "auf Port 8000"
    "im Ordner C:\\Users\\..."
    "" (wenn nicht bekannt)

Die Sprachansage wird daraus automatisch eine kurze, neutrale Bestaetigung
(z.B. "Einen Augenblick, the maintainer."). Es wird KEINE Mechanik genannt
("Sub-Agent", "delegiere", "OpenClaw", "spawn") und KEINE "Sir"-Anrede.
Mandat-A1: ausschliesslich "the maintainer". Audit F-AUDIT-1 (2026-04-29).
"""


class RouterBrain:
    """Main Jarvis router: thin wrapper around `BrainManager` in the router tier.

    The three categories (trivial / direct_action / spawn_worker) are
    decided IMPLICITLY via tool choice — the dispatcher tool-use loop in
    `BrainManager` handles the rest. This class itself contains no
    classification logic; that lives in `SYSTEM_PROMPT`.
    """

    def __init__(
        self,
        config: JarvisConfig,
        bus: EventBus,
        *,
        tools: dict[str, Tool],
        tool_executor: ToolExecutor,
        core_memory: CoreMemory | None = None,
        recall: RecallStore | None = None,
        user_profile: UserProfile | None = None,
        soul: Soul | None = None,
        people: PersonStore | None = None,
        vision_provider: VisionContextProvider | None = None,
    ) -> None:
        self._bus = bus
        self._vision = vision_provider
        self._manager = BrainManager.from_tier_config(
            "router",
            config=config,
            bus=bus,
            tools=tools,
            tool_executor=tool_executor,
            core_memory=core_memory,
            recall=recall,
            user_profile=user_profile,
            soul=soul,
            people=people,
        )
        # The router-specific system prompt is appended in `_build_system_prompt`
        # as the last layer before the base prompt. Replace the hardcoded
        # "Jarvis" with the configured name (no-op when the name is still
        # Jarvis) so that the router identity matches the persona.
        from .assistant_name import resolve_assistant_name

        _name = resolve_assistant_name(config)
        self._manager._system_prompt_extra = SYSTEM_PROMPT.replace(
            "Du bist Jarvis.", f"Du bist {_name}."
        )

    @property
    def manager(self) -> BrainManager:
        """Access to the underlying BrainManager (for tests/debug)."""
        return self._manager

    @property
    def active_provider(self) -> str:
        return self._manager.active_provider

    @property
    def tools(self) -> dict[str, Tool]:
        return self._manager._tools

    @property
    def system_prompt_extra(self) -> str:
        return self._manager._system_prompt_extra

    # ------------------------------------------------------------------
    # Perceived-latency acknowledgment hook
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_utterance_language(utterance: str) -> str:
        """Quick-and-dirty bilingual classifier for picking the ack language.

        German is the project default — anything ambiguous falls back to ``de``.
        We only flip to ``en`` when the utterance shows clear English structure
        (function words like ``the/what/how``) AND lacks German diacritics or
        common particles. Pure heuristic, regex-free, no dependencies.
        """
        if not utterance:
            return "de"
        text = utterance.lower()
        # Umlauts or sharp-s are an unambiguous German signal.
        if any(c in text for c in "äöüß"):
            return "de"
        de_markers = (" der ", " die ", " das ", " und ", " ich ", " du ",
                      " ist ", " auf ", " mit ", " nicht ", " für ", " wie ")
        en_markers = (" the ", " what ", " how ", " is ", " are ", " you ",
                      " can ", " could ", " would ", " please ", " do ")
        padded = " " + text + " "
        de_hits = sum(1 for m in de_markers if m in padded)
        en_hits = sum(1 for m in en_markers if m in padded)
        if en_hits >= 2 and de_hits == 0:
            return "en"
        return "de"

    def _build_ack_emitter(self, utterance: str):
        """Construct the async callback that publishes ``AnnouncementRequested``.

        Returns ``None`` when there is no bus to publish on, or when the
        utterance is a Voice-Control command (skip-category 3 from the
        dropdown spec).

        The emitter:

        * resolves the ack template via ``ack_generator.generate_ack`` using
          the language picked from the utterance;
        * suppresses the announcement entirely when the generator returns
          ``None`` (skip-list — passive reads, low-latency UI events);
        * publishes with ``priority="normal"`` so it queues behind any
          higher-priority interrupt without barging in itself.
        """
        if self._bus is None:
            return None
        if is_voice_control_utterance(utterance):
            return None
        bus = self._bus
        language = self._detect_utterance_language(utterance)

        async def emit(tool_name: str, tool_args: dict) -> None:
            text = generate_ack(tool_name, tool_args, language=language)
            if text is None:
                return
            await bus.publish(
                AnnouncementRequested(
                    text=text,
                    priority="normal",
                    language=language,
                    source_layer="brain.router.ack",
                )
            )

        return emit

    # ------------------------------------------------------------------
    # Streaming-Entrypoint
    # ------------------------------------------------------------------

    async def handle(
        self,
        utterance: str,
        *,
        history: list[BrainMessage] | None = None,
        trace_id: UUID | None = None,
    ) -> AsyncIterator[BrainDelta]:
        """Processes a user utterance and streams `BrainDelta` chunks.

        Tool use happens implicitly in the dispatcher. For TRIVIAL turns the
        brain streams text chunks; for DIRECT_ACTION / SPAWN_WORKER the
        dispatcher runs the tool-use loop and yields the final text responses
        after the tool result.

        Args:
            utterance: User text, verbatim (not rephrased).
            history: Optional message history (default: empty — the router is
                typically stateless per turn).
            trace_id: Optional for flight-recorder correlation.
        """
        brain = self._manager._get_brain(
            self._manager.active_provider,
            self._manager._fast_model(self._manager.active_provider),
        )
        dispatcher = self._manager._build_dispatcher(brain)

        # Permanent vision: inject a fresh screen observation as an ImageBlock
        # when the provider is available and not paused. Errors are not fatal
        # — the text-only fallback keeps the conversation running.
        images: tuple[ImageBlock, ...] = ()
        vision_none = self._vision is None
        paused = (
            bool(getattr(self._vision, "is_paused", False))
            if self._vision is not None
            else None
        )
        log.info(
            "Vision-Inject Diagnose: path=RouterBrain vision_none=%s "
            "is_paused=%s brain_provider=%s",
            vision_none,
            paused,
            self._manager.active_provider,
        )
        if self._vision is not None and not self._vision.is_paused:
            try:
                obs = await self._vision.current()
                hash_prefix = (obs.screenshot_hash or "")[:16]
                log.info(
                    "Vision-Inject Observation: screenshot_path=%s "
                    "screenshot_hash=%s window=%r",
                    obs.screenshot_path,
                    hash_prefix,
                    getattr(obs, "window_title", None),
                )
                mime, image_b64 = await _read_observation_image_b64(obs)
                log.info(
                    "Vision-Inject encoded: brain_provider=%s mime=%s "
                    "screenshot_hash=%s len_image_b64=%d",
                    self._manager.active_provider,
                    mime,
                    hash_prefix,
                    len(image_b64),
                )
                images = (
                    ImageBlock(
                        mime=mime,
                        data_b64=image_b64,
                        source_hash=obs.screenshot_hash,
                    ),
                )
                if self._bus is not None:
                    bytes_size = len(image_b64) * 3 // 4
                    age_ms = int((time.time_ns() - obs.timestamp_ns) / 1_000_000)
                    await self._bus.publish(VisionInjected(
                        trace_id=trace_id or obs.trace_id,
                        screenshot_hash=obs.screenshot_hash,
                        bytes_size=bytes_size,
                        capture_age_ms=age_ms,
                    ))
            except Exception as exc:  # noqa: BLE001
                # Laut loggen: Silent Text-Only-Fallback hat uns in Prod stumm
                # gemacht (User merkt nicht, dass Jarvis den Screen verloren
                # hat). exc_info=True schreibt Stacktrace in den Flight-Recorder.
                log.error(
                    "Vision-Inject fehlgeschlagen (%s) — Text-Only Fallback. "
                    "Pruefe ob VisionContextProvider.start() gelaufen ist und "
                    "data/flight_recorder/blobs/ beschreibbar ist.",
                    exc,
                    exc_info=True,
                )

        messages: list[BrainMessage] = list(history or [])
        messages.append(BrainMessage(role="user", content=utterance, images=images))

        tools_payload = dispatcher.tools_payload()
        system_prompt = self._manager._build_system_prompt()

        if self._manager._tools and self._manager._tool_executor is not None:
            # The tool-use loop aggregates internally; we yield the final
            # aggregate as a single delta (stream-compatible adapter). This
            # gives the caller a uniform AsyncIterator regardless of whether
            # a tool call or plain text was produced.
            ack_emitter = self._build_ack_emitter(utterance)
            agg = await dispatcher.dispatch(
                utterance,
                images=images,
                history=history,
                trace_id=trace_id,
                ack_emitter=ack_emitter,
            )
            # Perceived-latency completion marker. The user opted for an
            # unconditional "Erledigt." at the end of any turn that
            # actually executed tools — even if the brain's own response
            # already carries the substance, the trailing marker signals
            # "task done" cleanly. Trivial-path turns (no tool_calls) skip
            # this; Voice-Control utterances skip too (action == confirmation).
            final_text = agg.text or ""
            if agg.tool_calls and not is_voice_control_utterance(utterance):
                from .ack_generator import final_summary_marker
                lang = self._detect_utterance_language(utterance)
                marker = final_summary_marker(language=lang)
                if final_text.strip():
                    final_text = final_text.rstrip().rstrip(".") + ". " + marker
                else:
                    final_text = marker
            if final_text:
                yield BrainDelta(content=final_text)
            for tc in agg.tool_calls:
                yield BrainDelta(tool_call=tc)
            if agg.finish_reason:
                yield BrainDelta(
                    finish_reason=agg.finish_reason,
                    usage=agg.usage or None,
                )
            return

        # Simple mode: no tool executor — stream directly (images are already
        # included in the user BrainMessage above).
        req = BrainRequest(
            messages=tuple(messages),
            tools=tuple(tools_payload),
            system=system_prompt,
            stream=True,
        )
        async for delta in brain.complete(req):
            yield delta


async def _read_observation_image_b64(obs: Observation) -> tuple[str, str]:
    """Reads `Observation.screenshot_path` as a Base64-encoded image.

    Uses `asyncio.to_thread` for file I/O so the event loop is not blocked.
    If the observation has no path (e.g. from pure ui_tree mode), a
    `ValueError` is raised and the caller falls back to text-only.
    """
    import asyncio

    if obs.screenshot_path is None:
        raise ValueError("Observation ohne screenshot_path")
    path = obs.screenshot_path

    def _read() -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    data = await asyncio.to_thread(_read)
    return _detect_image_mime(data), base64.b64encode(data).decode("ascii")


def _detect_image_mime(data: bytes) -> str:
    """Determines the MIME type for the provider adapters."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise ValueError("Observation enthaelt kein unterstuetztes Bildformat")


async def _read_observation_png_b64(obs: Observation) -> str:
    """Backwards-compatible helper for old tests/callsites."""
    mime, data_b64 = await _read_observation_image_b64(obs)
    if mime != "image/png":
        raise ValueError(f"Observation ist {mime}, nicht image/png")
    return data_b64
