"""Three-valued visual-intent classification for Screen Context.

Answers one question about a user turn: *did the user unambiguously ask Jarvis
to look at the screen?* The three-valued answer is the point. A two-valued gate
must resolve "what is that?" as either a capture or a miss, and both are wrong
for that same sentence — it is a question about the screen roughly as often as
it is a question about the topic under discussion. ``AMBIGUOUS`` is what lets
Jarvis ask a one-line question instead of guessing, which is the behaviour this
feature promises.

**Deterministic by mandate.** Regex over a normalized string, O(1), no model
call and no network. An LLM classifier here would sit on the voice path (AP-9),
and it would make "did it look at my screen, and why?" unanswerable after the
fact — ``IntentVerdict.evidence`` exists so that question always has an answer.

**Every supported locale is equal** (CLAUDE.md §1): de, en and es are matched
simultaneously rather than selected by the session locale, because a bilingual
user mixes them inside one sentence and a locale-gated matcher would go deaf on
the half it did not pick. Adding a locale is a data entry below, not code.

The vocabulary is speech-recognition *input matching data* — the closed-list
exception in §1, the same category as ``jarvis/brain/cu_gate.py``.
"""
from __future__ import annotations

import logging
import re

from jarvis.screen_context.models import IntentVerdict, VisualIntent

log = logging.getLogger(__name__)

#: Cap on recorded evidence fragments — a log line, not a transcript.
_MAX_EVIDENCE = 3


# Questions *about* Screen Context are not consent to use it.  This guard is
# deliberately evaluated before the positive matcher: all of these sentences
# contain the same screen/look words as a real request, but their grammatical
# shape asks for instructions or product behaviour rather than a look now.
_SCREEN_CONTEXT_META_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- English ---
    r"\bhow\s+(?:do|can|could|would)\s+(?:i|you)\b[^.?!]{0,96}"
    r"\b(?:look|see|view|inspect|screen|screenshot|capture)\b"
    r"|\bhow\s+to\b[^.?!]{0,96}\b(?:screen|screenshot|capture)\b"
    r"|\bwhat\s+(?:happens|would\s+happen)\s+(?:if|when)\b[^.?!]{0,96}"
    r"\b(?:screen|screenshot|capture)\b"
    # --- German --- i18n-allow: German speech-input matching data
    r"|\bwie\s+(?:kann|koennte|wuerde)\s+(?:ich|man)\b[^.?!]{0,96}"  # i18n-allow
    r"\b(?:bildschirm|screen|screenshot|bildschirmfoto|ansehen|zeigen)\b"
    r"|\bwie\s+(?:kannst|koenntest|wuerdest)\s+du\b[^.?!]{0,96}"
    r"\b(?:bildschirm|screen|screenshot|bildschirmfoto|sehen|ansehen)\b"
    r"|\bwas\s+passiert\s+(?:wenn|falls)\b[^.?!]{0,96}"
    r"\b(?:bildschirm|screen|screenshot|bildschirmfoto)\b"
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\bcomo\s+(?:puedo|podria|puedes|podrias|se\s+puede)\b[^.?!]{0,96}"
    r"\b(?:pantalla|captura|ver|mirar)\b"
    r"|\bque\s+pasa\s+(?:si|cuando)\b[^.?!]{0,96}"
    r"\b(?:pantalla|captura)\b"
    r")",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

# German digraphs plus Spanish accent stripping, so the vocabulary below can be
# written in plain ASCII and still match accented input.  # i18n-allow
_TRANSLITERATION = str.maketrans(
    {
        # i18n-allow: character-mapping data for speech-input normalization
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",  # i18n-allow
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u", "ç": "c",
    }
)


# Idioms that CONTAIN a look-verb but are not a request to look at anything.
# Masked out before matching rather than used as a veto: a veto would let one
# stray idiom kill a turn that also contains a real request ("looks like an
# error — can you see this?"), which is exactly the sentence the feature exists
# for. Masking removes only the idiom and leaves the rest intact.
#
# This mirrors ``_PRODUCT_NAME_NOISE_RE`` in jarvis/brain/cu_gate.py, which
# learned the same lesson from a live incident: a bare verb token swallowed
# every phrase that merely contained it.
_IDIOM_NOISE_RE: re.Pattern[str] = re.compile(
    # --- English ---
    # "you see" is deliberately NOT masked bare: it is the tail of "can you
    # see this?", the single most common way to ask for a look in English.
    # Only its parenthetical and idiomatic forms are.
    r"\b(?:i|we|they)\s+see\b"
    r"|\bas\s+you\s+can\s+see\b|\byou\s+see\s*,|\bsee\s+you\b"
    r"|\blet(?:'|)s\s+see\b|\bwe(?:'|)ll\s+see\b|\bsee\s+if\b|\bsee\s+whether\b"
    r"|\blook(?:s|ed|ing)?\s+(?:for|into|up|like|after|forward)\b"
    r"|\bloo?ks?\s+(?:good|bad|fine|great|ok|okay)\b"
    r"|\bsee\s+(?:to\s+it|about)\b"
    # --- German --- i18n-allow: German speech-input matching data
    # "mal sehen" is NOT masked: "kannst du mal sehen?" is one of the two
    # phrasings this feature is specified around. On its own it matches no
    # request pattern anyway, so masking it would only cost the real request.
    r"|\bschauen\s+wir\b|\bsehen\s+wir\b"
    r"|\bwir\s+sehen\b|\bich\s+sehe\b|\bich\s+seh\b|\bwie\s+gesagt\b"
    r"|\bsieht\s+(?:aus|so\s+aus|gut\s+aus|schlecht\s+aus)\b|\baussehen\b"
    r"|\bsieh\s+zu,?\s+dass\b|\bschau\s+(?:nach|ob)\b"  # i18n-allow: DE input
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\bvamos\s+a\s+ver\b|\ba\s+ver\s+si\b|\bya\s+veo\b|\bveremos\b"
    r"|\bse\s+ve\b|\bmira\s+que\b|\bmira,?\s+(?:pero|es\s+que)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Casefold, transliterate, then blank out non-visual idioms.

    Substitutes a space rather than the empty string so surrounding word
    boundaries survive and a genuine request in the same sentence still
    matches.
    """
    folded = (text or "").casefold().translate(_TRANSLITERATION)
    return _IDIOM_NOISE_RE.sub(" ", folded)


# --------------------------------------------------------------------------
# Level 1 — window-scoped, unambiguous
# --------------------------------------------------------------------------

# A window/tab/dialog/app noun bound to a demonstrative or "active/current".
# The demonstrative is mandatory: a bare "window" is a common noun in every one
# of these languages ("a window of opportunity", "Fenster zum Hof"), and the
# whole point of this level is that the user pointed at something specific.
_WINDOW_SCOPE_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- English ---
    r"\b(?:this|that|the\s+(?:active|current|front|open))\s+"
    r"(?:window|tab|dialog|dialogue|app|application|document|file|page|sheet)\b"
    r"|\bin\s+(?:this|that)\s+(?:window|tab|dialog|app|document|page)\b"
    r"|\bthe\s+(?:window|tab|dialog|app)\s+(?:i(?:'|)m|i\s+am)\s+(?:in|on)\b"
    # --- German --- i18n-allow: German speech-input matching data
    # Demonstrative ONLY — a plain article is not a pointer: "Was kostet das
    # Programm?" and "die Seite gefaellt mir" are ordinary topic  # i18n-allow
    # nouns, and counting them made every mention of a program or  # i18n-allow
    # page nag the clarify question via the weak-signal tier.
    r"|\bdiese[srmn]?\s+"  # i18n-allow: DE input
    r"(?:fenster|tab|reiter|dialog|dialogfeld|anwendung|programm|dokument|seite)\b"
    r"(?=\s|$|[.,!?])"
    r"|\bim\s+(?:aktiven|aktuellen|offenen|vorderen)\s+fenster\b"
    r"|\bin\s+(?:diesem|dem)\s+(?:fenster|tab|reiter|dialog|dokument|programm)\b"
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\b(?:esta|esa|est[ae]\s+misma)\s+"
    r"(?:ventana|pestana|pagina|aplicacion|hoja)\b"
    r"|\b(?:este|ese)\s+(?:dialogo|documento|programa|archivo)\b"
    r"|\ben\s+(?:esta|esa)\s+(?:ventana|pestana|pagina|aplicacion)\b"
    r")",
    re.IGNORECASE,
)


# The German branch above deliberately requires a word boundary AFTER the noun
# via lookahead, because "die Seite" is also "the side/page of a book" — the
# demonstrative + noun pair is the signal, and a trailing genitive ("die Seite
# des Vertrags") should not scope a capture to a window.


# --------------------------------------------------------------------------
# Level 2 — screen-scoped, unambiguous
# --------------------------------------------------------------------------

# Spoken language puts articles, politeness and timing particles between a verb
# and its object ("mach MIR MAL EBEN EINEN Screenshot", "take A QUICK
# screenshot"). An enumerated filler list cannot keep up with that and the
# German pattern proved it: its single ``bitte`` slot missed the most common
# spoken phrasing of all ("mach mal einen Screenshot"), the turn classified as
# NONE, and Computer-Use picked it up and drove the desktop instead (BUG-124).
#
# A bounded any-token gap is the fix, not a longer word list. It is short
# enough that the verb and the noun still belong to one clause, and it stops at
# sentence punctuation so a verb in one sentence cannot bind a noun in the
# next. Same shape the German ``pruef``/``bildschirm`` rule below already uses.
_NEAR = r"[^.?!]{0,24}?"

# Clause end WITHOUT the comma. A deictic pronoun only points at the screen
# when it ends its clause ("was ist das?"); followed by a  # i18n-allow: quoted DE input
# determiner-bound content word the sentence is about that word, not the
# screen ("was ist das Beliebteste?" — live nag, voice  # i18n-allow: quoted DE input
# session 2026-08-06 18:33). The comma is excluded on purpose:
# "schau mal, ..." continues into a clause that owns the  # i18n-allow: quoted DE input
# real topic.
_END = r"\s*(?:$|[.?!;:])"

# Filler particles a spoken deictic may trail before the clause ends. These are
# the only tokens allowed between the pronoun and the end — anything else makes
# it a determiner. Matching data, not prose (CLAUDE.md §1 category 3).
_EN_TAIL = r"(?:\s+(?:then|exactly|now|again|here|there|really))*"
_DE_TAIL = (  # i18n-allow: German speech-input matching data
    r"(?:\s+(?:denn|eigentlich|jetzt|gerade|genau|nochmal|noch\s+mal"
    r"|ueberhaupt|hier|da))*"
)
_ES_TAIL = r"(?:\s+(?:exactamente|ahora|aqui|ahi|entonces))*"

_SCREEN_INTENT_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # ---------------- English ----------------
    # look/see verb bound to a deictic or to the screen itself
    r"\b(?:look|looking)\s+at\s+(?:this|that|it|the\s+screen|my\s+screen|the\s+error)\b"
    # "take/have a look" binds to a deictic or a screen surface. A generic
    # object ("take a look at the flight prices") is a research request, and
    # the objectless form only counts clause-final — "have a look, the prices
    # dropped" opens a statement, it does not order a look.
    r"|\b(?:take|have)\s+a\s+(?:quick\s+)?look\s+at\s+"
    r"(?:this|that|it|my\s+(?:screen|monitor)"
    r"|the\s+(?:screen|monitor|error|message|window|dialog|popup))\b"
    rf"|\bhave\s+a\s+(?:quick\s+)?look{_END}"
    r"|\bcheck\s+(?:this|that|it)\s+out\b"
    r"|\bcan\s+you\s+see\s+(?:this|that|it|my\s+screen|the\s+screen)\b"
    r"|\bdo\s+you\s+see\s+(?:this|that|it)\b"
    r"|\bsee\s+(?:this|that)\s+(?:here|error|message|window|screen)\b"
    r"|\bwhat\s+(?:do\s+you\s+see|am\s+i\s+looking\s+at)\b"
    # screen nouns with a possessive/demonstrative
    r"|\b(?:on|at)\s+(?:my|the|this)\s+screen\b|\bmy\s+screen\b"
    r"|\bwhat(?:'|)s\s+on\s+(?:my|the)\s+screen\b"
    r"|\bwhat\s+can\s+you\s+see\s+on\s+(?:my|the)\s+(?:screen|monitor)\b"
    r"|\banaly[sz]e\s+(?:(?:this|that|the|my)\s+)?"
    r"(?:screenshot|screen\s+capture)\b"
    r"|\b(?:inspect|review|analy[sz]e)\s+(?:this|that|the\s+current)\s+"
    r"(?:window|screen|monitor|dialog|tab)\b"
    # A screenshot request names the feature outright — the least ambiguous
    # sentence there is. Spoken language puts fillers between the verb and the
    # noun ("take a quick screenshot for me"), and the earlier tight pattern
    # missed every one of them; the turn then fell through to Computer-Use,
    # which drove the desktop instead of taking one picture (BUG-124).
    rf"|\b(?:take|make|capture|grab|get)\b{_NEAR}"
    r"\bscreen(?:shot|\s+capture)\b"
    r"|\bscreen(?:shot|\s+capture)\s+(?:this|that|my\s+screen|the\s+screen)\b"
    r"|\bscreen(?:shot|\s+capture)\s+(?:please|for\s+me)\b"
    # UI nouns need the demonstrative; bare "message" dropped — "did you get
    # that message from Anna?" is about mail, not pixels.
    r"|\b(?:this|that)\s+(?:error|error\s+message|button|menu|popup)\b"
    # read-out requests: the object is a deictic or an on-screen artefact. A
    # generic "the <noun>" ("what does the contract say", "read the news to
    # me") asks for content Jarvis should fetch, not for the screen.
    r"|\bwhat\s+does\s+(?:it|this|that"
    r"|the\s+(?:screen|error|message|dialog|popup|notification|window|page|button)"
    r")\s+say\b"
    rf"|\bread\s+(?:this|that|it)(?:\s+out)?(?:\s+aloud)?(?:\s+to\s+me)?{_END}"
    r"|\bread\s+(?:the|this|that)\s+"
    r"(?:screen|error|message|dialog|popup|notification)\b"
    r"|\bwhat\s+does\s+(?:this|that)\s+mean\s+(?:here|on\s+screen)\b"

    # ---------------- German ---------------- i18n-allow: German speech-input matching data
    # A look-verb takes a DEICTIC object ("schau dir das an"), never a
    # determiner phrase: in "schau dir das Video an" the sentence is about the
    # noun after "das", and a screenshot answers nothing about it. The deictic
    # therefore ends its clause (fillers allowed) or binds to the closing
    # "an"; a demonstrative may instead name a screen surface noun ("schau dir
    # dieses Fenster an"). Bare "schau mal" / "guck mal" count  # i18n-allow: quoted DE input
    # only clause-final — followed by content ("Schau mal, was  # i18n-allow: quoted DE input
    # kosten Fluege nach Rom?") they open a statement, they do  # i18n-allow: quoted DE input
    # not order a look (live over-trigger class, voice session
    # 2026-08-06 18:33).
    r"|\b(?:schau(?:e|st)?|guck(?:e|st)?|sieh(?:st)?)"
    r"(?:\s+(?:du|dir|mir|mal|bitte|kurz|eben|doch|noch))*\s+"
    r"(?:das|dies\w*|es)(?:\s+(?:mal|bitte|kurz|eben|doch|noch|dir|mir))*"  # i18n-allow
    rf"(?:\s+an\b|{_END})"
    r"|\b(?:schau(?:e|st)?|guck(?:e|st)?|sieh(?:st)?)"
    r"(?:\s+(?:du|dir|mir|mal|bitte|kurz|eben|doch|noch))*\s+"
    r"(?:auf\s+)?(?:das|den|dieses|diesen|meinen|deinen)\s+"  # i18n-allow: DE input
    r"(?:bildschirm|monitor|screen|display|fenster|tab|reiter|dialog"  # i18n-allow
    r"|fehlermeldung|fehler|meldung|popup|seite|dokument)\b"  # i18n-allow: DE input
    rf"|\b(?:schau|guck)\s+(?:mal\s+)?hier{_END}"
    rf"|\b(?:schau|guck|sieh)\s+(?:mal|her){_END}"
    rf"|\bkannst\s+du\s+(?:mal\s+)?(?:kurz\s+)?"
    rf"(?:sehen|schauen|gucken|draufschauen){_END}"
    r"|\bkannst\s+du\s+(?:das|dies|es"
    r"|meinen\s+(?:bildschirm|screen|monitor))\s+sehen\b"  # i18n-allow: DE input
    r"|\bkannst\s+du\s+(?:bitte\s+)?meinen\s+(?:bildschirm|monitor|screen|display)\s+"
    r"(?:anschauen|ansehen|pruefen)\b"  # i18n-allow: DE input
    rf"|\bsiehst\s+du\s+(?:das|dies)(?:\s+(?:hier|da|auch|gerade))*{_END}"  # i18n-allow
    r"|\bsiehst\s+du\s+(?:meinen\s+(?:bildschirm|screen)|den\s+fehler)\b"  # i18n-allow
    r"|\bwas\s+siehst\s+du\b"
    # Screen nouns in every case German inflects them into. The earlier list
    # spelled out the dative only, so the natural spoken accusative ("schau mal
    # auf meinEN Bildschirm") classified as NONE and the turn fell through to
    # Computer-Use (BUG-124). Inflection is matching data, not prose.
    # Anglicisms included: spoken German says "auf meinem Screen/Display" as
    # readily as "Bildschirm" — the miss sent a live turn into a blind
    # tool-loop instead of the one-shot look (voice session 2026-08-06 20:51).
    # i18n-allow: German speech-input matching data
    r"|\b(?:auf|an|in)\s+(?:dem|den|meinem|meinen|deinem|deinen|diesem|diesen)"  # i18n-allow: DE input
    r"\s+(?:bildschirm|monitor|screen|display)\b"  # i18n-allow: DE input
    r"|\bam\s+(?:bildschirm|monitor|screen|display)\b"  # i18n-allow: DE input
    r"|\bmein(?:en|em)?\s+(?:bildschirm|monitor|screen|display)\b"  # i18n-allow
    # i18n-allow: German speech-input matching data
    r"|\b(?:mach|mache|machst|erstell|erstelle|erstellst|nimm|schiess|schiesse)"
    rf"\b{_NEAR}"
    r"\b(?:screenshot|bildschirmfoto|bildschirmaufnahme)\b"  # i18n-allow: DE input
    r"|\b(?:screenshot|bildschirmfoto|bildschirmaufnahme)\s+"
    r"(?:machen|aufnehmen|erstellen|bitte)\b"  # i18n-allow: DE input
    r"|\bauf\s+dem\s+schirm\b|\bhier\s+auf\s+dem\b"  # i18n-allow: DE input
    r"|\b(?:diese|die)\s+fehlermeldung\b"  # i18n-allow: DE input
    r"|\b(?:dieser|der)\s+fehler\s+(?:hier|da)\b"  # i18n-allow: DE input
    # Demonstrative only: "der Knopf an meiner Jacke" and "das  # i18n-allow: quoted DE input
    # Menue im Restaurant" are ordinary nouns; only the pointing  # i18n-allow
    # form names the screen.
    r"|\bdieser\s+knopf\b|\bdieses\s+menue\b"  # i18n-allow: DE input
    # read-out requests
    r"|\bwas\s+steht\s+(?:da|hier|dort|drin)\b|\bda\s+steht\b"
    r"|\bwas\s+steht\s+(?:in|auf)\s+dies\w*\s+"
    r"(?:fenster|dialog|tab|dokument)\b"
    # The read-out object is a deictic or an on-screen artefact. "Lies mir
    # das Buch vor" / "lies die Nachrichten vor" ask for CONTENT  # i18n-allow: quoted DE input
    # (a book, the news), and bare "vorlesen" without a deictic  # i18n-allow: quoted DE input
    # belongs to them too.
    rf"|\blies\s+(?:mir\s+)?(?:bitte\s+)?(?:das|dies|es)"
    rf"(?:\s+(?:mal|bitte|kurz))*(?:\s+vor\b|{_END})"  # i18n-allow: DE input
    r"|\b(?:das|dies|es)\s+(?:bitte\s+)?(?:mal\s+)?vorlesen\b"  # i18n-allow: DE input
    r"|\blies\s+(?:mir\s+)?(?:bitte\s+)?(?:dies\w*|das)\s+"
    r"(?:fenster|dialog|tab|dokument)\b"  # i18n-allow: DE input
    r"|\bpruef\w*\s+(?:bitte\s+)?[^.?!]{0,72}\b"
    r"(?:bildschirm|monitor|fenster)\b"  # i18n-allow: DE input
    r"|\bwas\s+ist\s+das\s+(?:hier|da\s+auf)\b"  # i18n-allow: DE input

    # ---------------- Spanish ---------------- i18n-allow: Spanish speech-input matching data
    # "mira esta" alone is a determiner waiting for its noun ("mira esta
    # receta") — the demonstrative must name a screen surface to count, and
    # "echa un vistazo" with a generic object ("a los precios") is research.
    r"|\bmira\s+(?:esto|eso|la\s+pantalla|mi\s+pantalla|el\s+error)\b"
    r"|\bmira\s+(?:esta|esa)\s+(?:ventana|pestana|pagina|aplicacion|pantalla)\b"
    r"|\bmirar\s+(?:esto|eso)\b"
    r"|\becha(?:le)?\s+un\s+vistazo"
    rf"(?:\s+a\s+(?:esto|eso|la\s+pantalla|mi\s+pantalla))?{_END}"
    r"|\bpuedes\s+ver\s+(?:esto|eso|mi\s+pantalla|la\s+pantalla)\b"
    r"|\bves\s+(?:esto|eso|mi\s+pantalla)\b|\bque\s+ves\b"
    r"|\bque\s+ves\s+en\s+mi\s+(?:pantalla|monitor)\b"
    r"|\ben\s+(?:mi|la)\s+pantalla\b|\bmi\s+pantalla\b"
    rf"|\b(?:haz|hazme|toma|tomame|captura|saca)\b{_NEAR}"
    r"\bcaptura\s+de\s+pantalla\b"
    r"|\banaliza\s+(?:esta\s+|esa\s+|la\s+)?captura\s+de\s+pantalla\b"
    r"|\bque\s+dice\s+(?:ahi|aqui|esto|eso)\b"
    rf"|\blee\s+(?:esto|eso){_END}"
    r"|\blee\s+(?:el|este|ese)\s+(?:error|mensaje|dialogo|aviso)\b"
    r"|\beste\s+error\b|\besta\s+ventana\s+de\s+error\b"
    r")",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Ownership boundary — observing is not operating
# --------------------------------------------------------------------------

# Split only at command-sequencing boundaries. This lets an explanatory clause
# stay read-only while a later independent clause still owns its operation:
# "Explain this, then close the window." -> [observe, operate].
_COMMAND_CLAUSE_SPLIT_RE: re.Pattern[str] = re.compile(
    r"[.!?;]+|,\s*(?=(?:then|dann|luego)\b)|"
    r"\b(?:and\s+then|then|und\s+dann|dann|y\s+luego|luego)\b",
    re.IGNORECASE,
)

_EXPLICIT_COMPUTER_USE_RE: re.Pattern[str] = re.compile(
    r"\b(?:use|with|via|using)\s+(?:the\s+)?computer[-\s]?use\b"
    # i18n-allow: German speech-input matching data
    r"|\b(?:mit|per|ueber|via|nutz\w*|benutz\w*|verwend\w*)\s+"  # i18n-allow
    r"(?:der\s+|die\s+|das\s+|den\s+)?computer[-\s]?use\b",  # i18n-allow
    re.IGNORECASE,
)

_EN_ACTION = (
    r"(?:click|double-?click|right-?click|tap|scroll|drag|drop|hover|press|"
    r"type|enter|paste|select|choose|submit|zoom|save|delete|remove|download|"
    r"upload|open|close|refresh|reload|minimi[sz]e|maximi[sz]e|move|resize|"
    r"switch|navigate|log\s+(?:in|out))\w*"
)
_DE_ACTION = (
    # i18n-allow: German speech-input matching data
    r"(?:(?:an|drauf|darauf|doppel|rechts)?klick|(?:hoch|runter)?scroll|"
    r"(?:ein)?tipp|zieh|verschieb|drueck|(?:ein)?fueg|(?:aus)?waehl|markier|"  # i18n-allow
    r"kopier|reich\w*\s+ein|send\w*\s+ab|zoom|speicher|loesch|entfern|"
    r"lad\w*\s+(?:hoch|runter)|oeffn|schliess|aktualisier|neu\s+lad|"
    r"anmeld|abmeld|minimier|maximier|verkleiner|vergroesser|wechsel|navigier)\w*"
)
_ES_ACTION = (
    r"(?:haz\s+clic|cliquea|pincha|pulsa|teclea|escribe|pega|desplaza|arrastr|"
    r"selecciona|elige|envia|guarda|elimina|borra|descarga|sube|abre|cierra|"
    r"actualiza|recarga|inicia\s+sesion|cierra\s+sesion|minimiza|maximiza|"
    r"mueve|cambia|navega|amplia|reduce)\w*"
)

_ACTION_CONNECTOR_SPLIT_RE: re.Pattern[str] = re.compile(
    rf"\b(?:and|und|y)\s+(?=(?:{_EN_ACTION}|{_DE_ACTION}|{_ES_ACTION})\b)",
    re.IGNORECASE,
)

_OPERATION_EXPLANATION_CLAUSE_RE: re.Pattern[str] = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)?"
    r"(?:explain|show|tell)\b"
    # i18n-allow: German speech-input matching data
    r"|^(?:(?:kannst|koenntest|wuerdest|wirst)\s+du\s+(?:bitte\s+)?|bitte\s+)?"
    r"(?:erklaer|zeig|sag)\w*\b"
    r"|^(?:(?:puedes|podrias)\s+(?:por\s+favor\s+)?|por\s+favor\s+)?"
    r"(?:explica|muestra|dime)\b",
    re.IGNORECASE,
)

_SCREEN_OPERATION_CLAUSE_RE: re.Pattern[str] = re.compile(
    # English imperatives and request forms, including "would you mind clicking".
    rf"^(?:please\s+)?{_EN_ACTION}\b"
    rf"|^(?:can|could|will)\s+you\s+(?:please\s+)?[^.?!]{{0,72}}?{_EN_ACTION}\b"
    rf"|^would\s+you\s+(?:please\s+)?(?:mind\s+)?[^.?!]{{0,72}}?{_EN_ACTION}\b"
    rf"|^i\s+(?:want|need)\s+you\s+to\s+[^.?!]{{0,72}}?{_EN_ACTION}\b"
    # German: allow the object before the final infinitive ("Bitte den Knopf anklicken").
    rf"|^(?:bitte\s+)?{_DE_ACTION}\b"  # i18n-allow
    rf"|^bitte\s+[^.?!]{{0,96}}?{_DE_ACTION}\b"  # i18n-allow
    rf"|^(?:kannst|koenntest|wuerdest|wirst)\s+du\s+(?:bitte\s+)?"
    rf"[^.?!]{{0,96}}?{_DE_ACTION}\b"  # i18n-allow
    # Spanish imperative and modal request forms.
    rf"|^(?:por\s+favor\s+)?{_ES_ACTION}\b"
    rf"|^(?:puedes|podrias)\s+(?:por\s+favor\s+)?[^.?!]{{0,96}}?{_ES_ACTION}\b",
    re.IGNORECASE,
)

_ACTION_STATE_OBSERVATION_RE: re.Pattern[str] = re.compile(
    rf"^(?:{_EN_ACTION})\s+(?:is|are|was|were|looks?|seems?|appears?)\b"
    rf"|^(?:{_DE_ACTION})\s+(?:ist|sind|war|waren|scheint)\b"  # i18n-allow
    rf"|^(?:{_ES_ACTION})\s+(?:esta|estan|parece)\b",
    re.IGNORECASE,
)


def requests_screen_operation(text: str) -> bool:
    """Return whether the turn asks Jarvis to operate the live desktop.

    This is the hard ownership boundary between Screen Context and
    Computer-Use.  It is deliberately deterministic and multilingual: an
    operation request never captures a separate one-shot context first, while
    a question about what to operate remains a read-only look.
    """
    normalized = _normalize(text).strip()
    if not normalized:
        return False
    if _EXPLICIT_COMPUTER_USE_RE.search(normalized):
        return True
    for sequence in _COMMAND_CLAUSE_SPLIT_RE.split(normalized):
        for raw_clause in _ACTION_CONNECTOR_SPLIT_RE.split(sequence):
            clause = raw_clause.strip(" ,:\t\r\n")
            if not clause or _OPERATION_EXPLANATION_CLAUSE_RE.search(clause):
                continue
            if _ACTION_STATE_OBSERVATION_RE.search(clause):
                continue
            if _SCREEN_OPERATION_CLAUSE_RE.search(clause):
                return True
    return False


# --------------------------------------------------------------------------
# Level 3 — weak signals: ask, never capture
# --------------------------------------------------------------------------

# A bare deictic with no visual anchor, or an objectless look-verb. These are
# the sentences a capture-happy heuristic gets wrong in BOTH directions, so
# they earn a question rather than a guess.
#
# The deictic must be a PRONOUN, which in speech means it ends its clause
# (filler particles allowed). Followed by a content word it is a determiner
# and the sentence is about that word: "Was ist das  # i18n-allow: quoted DE input
# Beliebteste?" continued a boxing conversation, classified as  # i18n-allow: quoted DE input
# a screen question, and Jarvis derailed the topic twice with
# its clarifying question (live nag, voice session 2026-08-06
# 18:33). The same shape applies to every branch —
# and to objectless "can you check", which with an object ("can you check the
# weather?") is a lookup request, not a look request.
_AMBIGUOUS_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- English ---
    rf"\bwhat\s+(?:is|'s)\s+(?:this|that){_EN_TAIL}{_END}"
    rf"|\bwhat\s+about\s+(?:this|that){_EN_TAIL}{_END}"
    rf"|\band\s+(?:this|that)\s+one{_EN_TAIL}{_END}"
    rf"|\bwhy\s+is\s+(?:this|that){_EN_TAIL}{_END}"
    rf"|\bis\s+(?:this|that)\s+(?:right|correct|ok|okay){_EN_TAIL}{_END}"
    rf"|\bcan\s+you\s+(?:check|have\s+a\s+look){_END}"
    rf"|\bcheck\s+(?:this|that){_EN_TAIL}{_END}"
    rf"|\bwhat\s+does\s+(?:this|that)\s+mean{_EN_TAIL}{_END}"
    # --- German --- i18n-allow: German speech-input matching data
    rf"|\bwas\s+ist\s+(?:das|dies){_DE_TAIL}{_END}"  # i18n-allow: DE input
    r"|\bund\s+(?:das|dies)\s*(?:da|hier)?\s*[.?!]?\s*$"
    rf"|\bwarum\s+ist\s+(?:das|dies){_DE_TAIL}{_END}"  # i18n-allow: DE input
    rf"|\bstimmt\s+(?:das|dies){_DE_TAIL}{_END}"  # i18n-allow: DE input
    r"|\bkannst\s+du\s+(?:das|dies)\s+(?:mal\s+)?(?:pruefen|checken|kontrollieren)\b"
    rf"|\bwas\s+(?:bedeutet|heisst)\s+(?:das|dies){_DE_TAIL}{_END}"
    r"|\bschau\s+(?:da\s+)?(?:mal\s+)?drauf\b"
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    rf"|\bque\s+es\s+(?:esto|eso){_ES_TAIL}{_END}"
    rf"|\by\s+(?:esto|eso){_ES_TAIL}{_END}"
    rf"|\bpor\s+que\s+es\s+(?:esto|eso){_ES_TAIL}{_END}"
    rf"|\besta\s+bien\s+(?:esto|eso){_ES_TAIL}{_END}"
    rf"|\bpuedes\s+(?:revisar|comprobar)(?:\s+(?:esto|eso))?{_END}"
    rf"|\bque\s+significa\s+(?:esto|eso){_ES_TAIL}{_END}"
    r")",
    re.IGNORECASE,
)


def _evidence(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    """Up to ``_MAX_EVIDENCE`` matched fragments, whitespace-collapsed."""
    found: list[str] = []
    for match in pattern.finditer(text):
        fragment = " ".join(match.group(0).split())
        if fragment and fragment not in found:
            found.append(fragment)
        if len(found) >= _MAX_EVIDENCE:
            break
    return tuple(found)


def classify(text: str, *, locale: str = "") -> IntentVerdict:
    """Classify one user turn's visual intent.

    Precedence is most-specific-first: a window-scoped reference wins over a
    plain screen reference (it says *which* surface), and any unambiguous
    reference wins over a weak one. An empty or whitespace-only turn is
    ``NONE`` — non-conversational callers (REST, a scheduled task) reach the
    service without an utterance and must not be read as having asked to look.
    """
    normalized = _normalize(text).strip()
    if not normalized:
        return IntentVerdict(intent=VisualIntent.NONE, locale=locale)

    if _SCREEN_CONTEXT_META_RE.search(normalized):
        return IntentVerdict(intent=VisualIntent.NONE, locale=locale)

    # Operating the desktop is Computer-Use's job.  This check precedes every
    # visual noun match so phrases such as "click the button on my screen" do
    # not take a one-shot screenshot and then disable the very tools requested.
    if requests_screen_operation(normalized):
        return IntentVerdict(intent=VisualIntent.NONE, locale=locale)

    window_hits = _evidence(_WINDOW_SCOPE_RE, normalized)
    screen_hits = _evidence(_SCREEN_INTENT_RE, normalized)

    # A window noun alone is only a scope, not a request. It upgrades an
    # unambiguous screen request to window scope, and on its own ("close this
    # window") it is a desktop command for Computer-Use, not a look request.
    if window_hits and screen_hits:
        return IntentVerdict(
            intent=VisualIntent.WINDOW,
            evidence=(window_hits + screen_hits)[:_MAX_EVIDENCE],
            locale=locale,
        )
    if screen_hits:
        return IntentVerdict(
            intent=VisualIntent.SCREEN, evidence=screen_hits, locale=locale
        )

    ambiguous_hits = _evidence(_AMBIGUOUS_RE, normalized)
    # A window reference with no look verb is weak, not absent: "is this tab
    # right?" plausibly wants a look. Ask.
    if window_hits or ambiguous_hits:
        return IntentVerdict(
            intent=VisualIntent.AMBIGUOUS,
            evidence=(ambiguous_hits + window_hits)[:_MAX_EVIDENCE],
            locale=locale,
        )

    return IntentVerdict(intent=VisualIntent.NONE, locale=locale)


# --------------------------------------------------------------------------
# The clarifying question
# --------------------------------------------------------------------------

# One entry per supported locale, keyed by the BCP-47 base tag. Adding a locale
# means adding a line here AND to the matcher above — never a de/en-only table
# (CLAUDE.md §1.3). The phrasing is deliberately a yes/no question about
# looking, so a bare "yes" in the next turn is a complete answer.
# i18n-allow: this IS the runtime multilingual product surface (§1 category 1)
_CLARIFY_QUESTION: dict[str, str] = {
    "en": "Should I take a look at your screen?",
    "de": "Soll ich einmal auf deinen Bildschirm schauen?",  # i18n-allow: spoken
    "es": "¿Quieres que eche un vistazo a tu pantalla?",
}

_CANCELLED_REPLY: dict[str, str] = {
    "en": "Okay, I won't look at your screen.",
    "de": "Okay, ich schaue nicht auf deinen Bildschirm.",  # i18n-allow: spoken
    "es": "De acuerdo, no miraré tu pantalla.",
}

_NO_VISION_PROVIDER_REPLY: dict[str, str] = {
    "en": (
        "I captured the screen, but none of your available assistants can "
        "inspect images right now. Connect a vision-capable provider in API Keys."
    ),
    "de": (
        "Ich habe den Bildschirm aufgenommen, aber keiner deiner verfügbaren "  # i18n-allow
        "Assistenten kann gerade Bilder auswerten. Verbinde unter API-Keys "  # i18n-allow
        "einen Anbieter mit Bildverarbeitung."  # i18n-allow
    ),  # i18n-allow: spoken
    "es": (
        "Capturé la pantalla, pero ninguno de tus asistentes disponibles puede "
        "analizar imágenes ahora. Conecta un proveedor con visión en Claves API."
    ),
}

_CAPTURE_FAILURE_REPLY: dict[str, str] = {
    "en": (
        "I could not inspect the screen safely because Screen Context failed. "
        "No screenshot was attached."
    ),
    "de": (
        "Ich konnte den Bildschirm nicht sicher prüfen, weil der "  # i18n-allow
        "Bildschirmkontext fehlgeschlagen ist. Es wurde kein Screenshot "  # i18n-allow
        "angehängt."  # i18n-allow
    ),  # i18n-allow: spoken
    "es": (
        "No pude revisar la pantalla de forma segura porque falló el contexto "
        "de pantalla. No se adjuntó ninguna captura."
    ),
}

_CAPTURE_UNAVAILABLE_REPLY: dict[str, dict[str, str]] = {
    "en": {
        "capture_permission": (
            "I cannot capture your screen until Screen Recording is allowed in "
            "system settings. Grant that permission and ask again. No desktop "
            "action was started."
        ),
        "wayland_portal": (
            "Screen capture is unavailable in this Wayland session. Install and "
            "enable an XDG desktop portal for your compositor, or sign in with "
            "an X11 session, then ask again. No desktop action was started."
        ),
        "no_display": (
            "I cannot access an interactive display on this device. Sign in to "
            "a desktop session or connect a display, then ask again. No desktop "
            "action was started."
        ),
        "capture_backend_unavailable": (
            "The screen-capture backend is unavailable right now. Restart the "
            "desktop app and ask again. No desktop action was started."
        ),
    },
    "de": {  # i18n-allow: spoken
        "capture_permission": (
            "Ich kann deinen Bildschirm erst aufnehmen, wenn die "  # i18n-allow
            "Bildschirmaufnahme in den Systemeinstellungen erlaubt ist. "  # i18n-allow
            "Erteile die Berechtigung und frag noch einmal. Es wurde keine "  # i18n-allow
            "Desktop-Aktion gestartet."  # i18n-allow
        ),
        "wayland_portal": (
            "Die Bildschirmaufnahme ist in dieser Wayland-Sitzung nicht "  # i18n-allow
            "verfügbar. Installiere und aktiviere ein XDG-Desktop-Portal für "  # i18n-allow
            "deinen Compositor oder melde dich über X11 an und frag noch "  # i18n-allow
            "einmal. Es wurde keine Desktop-Aktion gestartet."  # i18n-allow
        ),
        "no_display": (
            "Ich kann auf diesem Gerät gerade keine interaktive Anzeige "  # i18n-allow
            "erreichen. Melde dich an einer Desktop-Sitzung an oder verbinde "  # i18n-allow
            "einen Bildschirm und frag noch einmal. Es wurde keine "  # i18n-allow
            "Desktop-Aktion gestartet."  # i18n-allow
        ),
        "capture_backend_unavailable": (
            "Die Bildschirmaufnahme ist gerade nicht verfügbar. Starte die "  # i18n-allow
            "Desktop-App neu und frag noch einmal. Es wurde keine "  # i18n-allow
            "Desktop-Aktion gestartet."  # i18n-allow
        ),
    },
    "es": {
        "capture_permission": (
            "No puedo capturar tu pantalla hasta que permitas la grabación de "
            "pantalla en los ajustes del sistema. Concede el permiso y vuelve a "
            "pedirlo. No se inició ninguna acción de escritorio."
        ),
        "wayland_portal": (
            "La captura de pantalla no está disponible en esta sesión de "
            "Wayland. Instala y activa un portal de escritorio XDG para tu "
            "compositor, o inicia una sesión X11, y vuelve a pedirlo. No se "
            "inició ninguna acción de escritorio."
        ),
        "no_display": (
            "No puedo acceder a una pantalla interactiva en este dispositivo. "
            "Inicia una sesión de escritorio o conecta una pantalla y vuelve a "
            "pedirlo. No se inició ninguna acción de escritorio."
        ),
        "capture_backend_unavailable": (
            "El sistema de captura de pantalla no está disponible ahora. "
            "Reinicia la aplicación de escritorio y vuelve a pedirlo. No se "
            "inició ninguna acción de escritorio."
        ),
    },
}

#: Fallback locale for a language with no entry — the repo-wide default.
_FALLBACK_LOCALE = "en"


def clarifying_question(locale: str) -> str:
    """The question Jarvis asks on an ``AMBIGUOUS`` verdict, in ``locale``.

    Takes an ALREADY-RESOLVED locale. This function must never re-derive the
    output language from the utterance — that is
    ``jarvis.core.turn_language.resolve_output_language``'s single job, and a
    second derivation here is exactly the mid-session language flip §1.3
    forbids.
    """
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CLARIFY_QUESTION.get(base) or _CLARIFY_QUESTION[_FALLBACK_LOCALE]


def cancelled_reply(locale: str) -> str:
    """The localized outcome after the user vetoes a proposed screen look."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CANCELLED_REPLY.get(base) or _CANCELLED_REPLY[_FALLBACK_LOCALE]


def no_vision_provider_reply(locale: str) -> str:
    """Honest fallback when no configured brain can inspect an attached image."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return (
        _NO_VISION_PROVIDER_REPLY.get(base)
        or _NO_VISION_PROVIDER_REPLY[_FALLBACK_LOCALE]
    )


def capture_failure_reply(locale: str) -> str:
    """Honest fail-closed reply for an unexpected Screen Context defect."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CAPTURE_FAILURE_REPLY.get(base) or _CAPTURE_FAILURE_REPLY[_FALLBACK_LOCALE]


def capture_unavailable_reply(locale: str, reason_code: str = "") -> str:
    """Honest no-action reply when this host cannot provide screen evidence."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    phrases = _CAPTURE_UNAVAILABLE_REPLY.get(base) or _CAPTURE_UNAVAILABLE_REPLY[
        _FALLBACK_LOCALE
    ]
    fallback = "capture_backend_unavailable"
    return phrases.get(reason_code) or phrases[fallback]


#: Locales with a clarifying question. Used by the parity test that pins this
#: table to the supported-locale list, so a new locale cannot ship half-wired.
SUPPORTED_CLARIFY_LOCALES: frozenset[str] = frozenset(_CLARIFY_QUESTION)


__all__ = [
    "SUPPORTED_CLARIFY_LOCALES",
    "cancelled_reply",
    "capture_failure_reply",
    "capture_unavailable_reply",
    "clarifying_question",
    "classify",
    "no_vision_provider_reply",
    "requests_screen_operation",
]
