"""Input vocabulary for the personal-memory relevance gate.

MATCHING DATA, NOT PROSE. Every token below is a fragment the speech
recogniser may literally produce, which a classifier must contain in order to
recognise the corresponding utterance — the same category as the router,
navigation-intent and wake-trigger vocabularies (CLAUDE.md §1, closed-list
item 3). Translating these tokens would make the gate deaf in that language.

All tokens are written PRE-FOLDED: lower-case, no umlauts, no accents, sharp-s
expanded. ``jarvis.brain.wiki_relevance.fold_text`` applies the same folding
to incoming text, so "Wofür" and "wofuer" both reach the "wofuer" token here.

de / en / es are equal peers. Adding a language means adding its tokens to
every tuple below — never a new branch in the logic module.
"""

from __future__ import annotations

__all__ = [
    "PERSONAL_MARKERS",
    "RECOLLECTION_PHRASES",
    "STRONG_RECALL_PHRASES",
    "PLANNING_PHRASES",
    "GENERAL_KNOWLEDGE_PHRASES",
    "LOOKUP_MARKERS",
    "STOPWORDS",
]


#: Ownership / self-reference. What separates "what are the billing rules"
#: (world knowledge) from "what are MY billing rules" (a memory question).
PERSONAL_MARKERS: tuple[str, ...] = (
    # de
    "mein", "meine", "meinem", "meinen", "meiner", "meines",
    "ich", "mir", "mich",
    "wir", "uns", "unser", "unsere", "unserem", "unseren", "unserer", "unseres",
    # en
    "my", "mine", "our", "ours", "i", "me", "we", "us",
    # es
    "mi", "mis", "mio", "mia", "nuestro", "nuestra", "nuestros", "nuestras",
    "yo", "nosotros",
)

#: Explicit recollection phrasing — an unambiguous request to the memory,
#: strong enough to consult even without an ownership marker.
RECOLLECTION_PHRASES: tuple[str, ...] = (
    # de
    "weisst du noch", "erinnerst du", "erinnere ich", "erinnerung",
    "hatten wir", "waren wir", "war ich", "hab ich", "habe ich",
    "wann war", "wann hatte", "wie hiess", "wie heisst",
    # en
    "do you remember", "remember when", "did i", "have i", "was i",
    "were we", "did we", "what did i", "when did i", "when was i",
    # es
    "te acuerdas", "recuerdas", "cuando fui", "cuando estuve",
    # "again"-shaped re-ask, all languages: the user once knew the answer and
    # is asking the memory to restore it ("wie hiess das nochmal", "what was
    # that called again", "como se llamaba"). The bare adverb is enough as a
    # RECALL signal because gates 2+3 still decide what may be injected.
    "nochmal", "nochmals", "noch mal", "noch gleich",
    "was that again", "called again", "name again",
    "como se llamaba", "otra vez como",
)

#: The UNAMBIGUOUS subset of the recollection phrasing, for callers where a
#: false positive is expensive (the realtime turn planner: a delegation costs
#: the user many seconds of silence, so broad members of
#: :data:`RECOLLECTION_PHRASES` like "habe ich" must not force one). Every
#: token here is a shape that practically only occurs when the user asks the
#: assistant to bring back something from their own past.
STRONG_RECALL_PHRASES: tuple[str, ...] = (
    # de
    "weisst du noch", "erinnerst du dich", "erinnerst du",
    "wann war ich", "wann waren wir", "wann hatte ich", "wann hatten wir",
    "wie hiess", "wie hiess nochmal", "was war nochmal", "wer war nochmal",
    "wie heisst nochmal", "wie war nochmal", "was ist nochmal",
    "was habe ich dir", "was hatte ich",
    # en
    "do you remember", "remember when", "when did i", "when was i",
    "when were we", "what was that again", "what was it again",
    "called again", "what did i tell you",
    # es
    "te acuerdas", "recuerdas cuando", "cuando fui", "cuando estuve",
    "como se llamaba",
)

#: Planning / recommendation / decision shape — the second turn class that is
#: worth a memory lookup even without a possessive. "What should I do", "what
#: is the fastest way to X", "any ideas for the weekend": the user is asking
#: for a course of action, and a course of action for THIS person depends on
#: what is known about them. These fire on their own (like the recollection
#: phrases), so they are deliberately COMPOUND rather than bare superlatives:
#: "fastest way" is an advice request, "fastest animal" is a quiz question.
#: Firing here only opens the retrieval — the post-search relevance filter and
#: the framed injection still decide whether anything reaches the model, so a
#: planning turn with no matching note produces silence exactly as before.
PLANNING_PHRASES: tuple[str, ...] = (
    # de
    "was soll ich", "was sollen wir", "soll ich", "sollte ich", "sollen wir",
    "was mache ich", "was machen wir", "was tue ich",
    "wie gehe ich vor", "wie gehe ich am besten", "wie kann ich am besten",
    "am besten", "beste weg", "besten weg", "bester weg",
    "schnellste weg", "schnellsten weg", "einfachste weg", "einfachsten weg",
    "beste option", "beste moeglichkeit", "welche option",
    "hast du ideen", "irgendwelche ideen", "ideen fuer", "idee fuer",
    "vorschlaege", "vorschlag fuer", "tipps fuer", "tipp fuer",
    "empfiehl", "empfiehlst du", "empfehlen", "empfehlung",
    "was wuerdest du", "was raetst du", "rate mir",
    "lohnt sich", "lohnt es sich",
    "hilf mir", "entscheiden", "entscheidung", "plan fuer", "planen wir",
    # en
    "what should i", "what should we", "should i", "should we",
    "what do i do", "what do we do", "how should i", "how should we",
    "what would you do", "what would you recommend", "how do i best",
    "best way", "fastest way", "quickest way", "easiest way", "smartest way",
    "best option", "best approach", "which option",
    "any ideas", "some ideas", "ideas for", "any suggestions",
    "suggestions for", "any tips", "tips for", "any advice", "advice on",
    "recommend", "recommendation", "recommendations",
    "help me decide", "help me plan", "plan my", "plan for",
    "worth it", "is it worth",
    # es
    "que deberia", "deberia", "deberiamos", "que hago", "que hacemos",
    "mejor manera", "mejor forma", "mejor opcion", "mejor camino",
    "manera mas rapida", "forma mas rapida",
    "alguna idea", "algunas ideas", "ideas para", "alguna sugerencia",
    "sugerencias para", "consejos para", "algun consejo",
    "recomienda", "recomiendas", "recomendacion", "recomendaciones",
    "que harias", "vale la pena", "ayudame a decidir",
)

#: Definitional / general-knowledge shape. On its own this asks about the
#: world, not about the user — the tallest-tower case. Only an ownership
#: marker turns such a question into a memory question.
GENERAL_KNOWLEDGE_PHRASES: tuple[str, ...] = (
    # de
    "was ist", "was sind", "was war", "was bedeutet", "wofuer steht",
    "wer ist", "wer war", "wer hat", "wie funktioniert",
    "wie viel", "wie viele", "wie hoch", "wie gross", "wie weit", "wie lange",
    "warum ist", "warum gibt", "wo liegt", "wo befindet", "erklaer",
    # en
    "what is", "what are", "what was", "what does", "what do",
    "who is", "who was", "how does", "how do", "how much", "how many",
    "how high", "how tall", "how big", "how far", "how long",
    "why is", "where is", "explain", "define",
    # es
    "que es", "que son", "que significa", "quien es", "quien fue",
    "como funciona", "cuanto mide", "cuantos", "donde esta", "explica",
)

#: Question / lookup shape. Combined with an ownership marker this is a memory
#: question; on its own it is not enough.
LOOKUP_MARKERS: tuple[str, ...] = (
    # de
    "wann", "was", "wer", "wen", "wem", "wessen",
    "welche", "welcher", "welches", "welchen", "welchem",
    "wo", "wie", "warum", "wieso",
    # German pronominal adverbs ("woran arbeite ich"): a whole question shape
    # with no single-word English equivalent. Missing these made "woran
    # arbeite ich gerade an X?" read as having no lookup shape at all —
    # caught by the live vault check on 2026-07-25, not by the unit tests.
    "woran", "worauf", "worueber", "womit", "wofuer", "wovon", "wobei",
    "wonach", "worin", "woher", "wohin",
    "kennst du", "zeig", "zeige", "nenn", "nenne",
    "erzaehl", "erzaehle", "sag mir", "liste",
    # en
    "when", "what", "who", "whom", "whose", "which", "where", "how", "why",
    "do you know", "tell me", "show me", "list", "remind me",
    # es
    "cuando", "que", "quien", "cual", "cuales", "donde", "adonde",
    "como", "por que", "cuanto", "cuanta", "cuantas",
    "dime", "muestrame",
)

#: Function words and generic question verbs, PRE-FOLDED like everything in
#: this module. Shared by keyword extraction (``wiki_context``) and coverage
#: counting (``wiki_relevance.content_terms``): a term that appears in every
#: second sentence carries no evidence about WHICH page answers the question,
#: so keeping it in a query or a coverage denominator only dilutes both.
#: Lived in ``wiki_context`` before, where it was compared against UNFOLDED
#: input — real umlaut spellings sailed straight past entries like "fuer" and
#: "ueber" and became junk keywords (root cause of the injector's
#: ``no_relevant_hits`` misses on German turns).
STOPWORDS: frozenset[str] = frozenset({
    # German
    "aber", "alle", "allem", "allen", "aller", "alles", "also", "ander",
    "andere", "anderem", "anderen", "anderer", "anderes", "anderm", "andern",
    "anderr", "anders", "auch", "auf", "aus", "bald", "beime", "beim",
    "bereits", "bin", "bist", "bitte", "bzw", "dabei", "dadurch", "damit",
    "dann", "dass", "dein", "deine", "deinem", "deinen", "deiner", "deines",
    "denen", "denn", "derer", "dessen", "dies", "diese", "diesem", "diesen",
    "dieser", "dieses", "doch", "durch", "ein", "eine", "einem", "einen",
    "einer", "eines", "einig", "einige", "einigem", "einigen", "einiger",
    "einiges", "einmal", "erst", "etwa", "euch", "euer", "eure", "eurem",
    "euren", "eurer", "eures", "falls", "fast", "fuer", "ganz", "gemacht",
    "gibt", "hatte", "haben", "habe", "habt", "hier", "hinter", "ihnen",
    "ihrer", "ihrem", "ihres", "ihren", "indem", "irgend", "ist", "jede",
    "jedem", "jeden", "jeder", "jedes", "jetzt", "kein", "keine", "keinem",
    "keinen", "keiner", "keines", "kann", "kannst", "konnte", "koennen",
    "macht", "manche", "manchem", "manchen", "mancher", "manches", "mein",
    "meine", "meinem", "meinen", "meiner", "meines", "mehr", "mich", "muss",
    "nach", "nicht", "noch", "oder", "ohne", "sehr", "sein", "seine",
    "seinem", "seinen", "seiner", "seines", "seit", "selbst", "sich", "sie",
    "sind", "soll", "sollen", "sollte", "sondern", "sonst", "ueber", "und",
    "unser", "unsere", "unserem", "unseren", "unserer", "unseres", "unter",
    "viel", "viele", "vielem", "vielen", "vieler", "vieles", "vom", "von",
    "vor", "wann", "ward", "warum", "was", "weg", "weil", "welche", "welchem",
    "welchen", "welcher", "welches", "wenn", "wer", "werden", "wie", "wieder",
    "will", "wird", "wirst", "wohl", "worden", "wurden", "wurde",
    "zwar", "zwischen",
    # Generic German question/event verbs — "wie hiess X nochmal", "was ist
    # bei Y passiert": the verb names the QUESTION SHAPE, never the page.
    "heisst", "heissen", "hiess", "hiessen", "genannt", "nochmal",
    "nochmals", "passiert", "passierte", "gewesen", "geworden", "gehabt",
    "brauche", "brauchst", "brauchen", "gebraucht",
    # German pronominal question adverbs and discourse fillers — pure
    # question shape ("Wofür brauche ich X?" is about X, never about
    # "wofuer").  # i18n-allow: quoted German utterance
    "wofuer", "worueber", "woran", "worauf", "womit", "wovon", "wobei",
    "wonach", "worin", "woher", "wohin", "wieso", "weshalb",
    "eigentlich", "zuletzt", "uebrigens", "vielleicht",
    # English
    "about", "above", "after", "again", "against", "among", "any",
    "are", "because", "been", "before", "being", "between", "both", "but",
    "came", "can", "come", "could", "did", "does", "doing", "done", "down",
    "during", "each", "few", "for", "from", "further", "gave", "get", "give",
    "goes", "going", "gone", "got", "had", "has", "have", "having", "here",
    "him", "his", "how", "into", "its", "just", "know", "like", "long",
    "look", "make", "many", "more", "most", "much", "must", "need", "new",
    "next", "not", "now", "old", "once", "only", "other", "our", "out",
    "over", "same", "say", "should", "since", "some", "still", "such",
    "tell", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "though", "through", "time", "told", "too",
    "under", "until", "upon", "use", "used", "using", "very", "want", "well",
    "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "would", "you", "your",
    # Generic English question/event verbs and fillers (mirror of the German
    # block above)
    "called", "named", "happened", "happen", "actually", "really",
    "basically", "anyway", "maybe",
    # Short German articles and pronouns
    "das", "dem", "den", "der", "des", "die", "dir", "du",
    "hat", "ich", "ihm", "ihn", "ihr", "ins", "man", "mir", "mit",
    "nun", "nur", "pro", "sei", "uns", "war", "wir", "wen",
    "zum", "zur",
    # Spanish function words and generic question verbs — es is an equal peer
    "para", "pero", "porque", "como", "cuando", "donde", "quien", "cual",
    "esta", "este", "esto", "estas", "estos", "son", "una", "uno", "unos",
    "unas", "los", "las", "del", "con", "sin", "sobre", "entre", "desde",
    "hasta", "muy", "mas", "menos", "tambien", "llama", "llamaba", "paso",
    "pasado", "realmente", "necesito", "necesitas", "quizas",
})
