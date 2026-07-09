**\# PersonalJarvis — Wake-Word-Fix — Handoff für den Hauptrechner**

\> **\*\*Diese Datei ist ein vollständiger Auftrag/Handoff.\*\*** Ein Coding-Agent (oder du selbst)  
\> auf dem **\*\*Hauptrechner\*\*** soll damit den Wake-Word-Bug endgültig fixen — so, dass er  
\> **\*\*mit jedem Wort auf jedem Rechner\*\*** funktioniert und **\*\*nicht mehr wiederkommt\*\***.  
\> Alles hier ist verifiziert und paste-fertig.

**\---**

**\#\# 0\. Kontext: Wer hat das erstellt und wie?**

\- **\*\*Erstellt von einem Agenten auf einem NEBENRECHNER\*\*** (nicht dem Hauptrechner).  
\- Der Nebenrechner hat die **\*\*laufende, gemanagte Installation\*\*** unter  
  \`C:\\Users\\haral\\.personal-jarvis\\\` (Git-Repo, \`origin\` \= silent backup, entspricht  
  aber byte-genau dem öffentlichen \`main\`).  
\- **\*\*Es wurde NICHTS verändert\*\*** — reine Read-only-Diagnose (Logs gelesen, Config gelesen,  
  Quellcode gelesen, zwei read-only Python-Probes gegen das Vosk-Modell laufen lassen).  
  Der Grund: Der Fix soll bewusst auf dem **\*\*Hauptrechner\*\*** passieren und von dort über die  
  Privacy-Gate nach GitHub gepusht werden.  
\- **\*\*Code-Stand identisch mit GitHub:\*\***  
  \- Lokaler \`HEAD\` \= \`1693874cf77e0548a3f4dbb6974f1c194a4e8ec0\` (Release v1.0.4)  
  \- \`origin/main\` \= \`1693874…\` → **\*\*byte-identisch, kein Code-Unterschied.\*\***  
  \- Der Bug lag **\*\*nicht im Code-Sync\*\***, sondern in der Engine-Auswahl-Logik \+ lokaler Config.  
  \- \`jarvis.toml\` ist git-ignoriert → liegt nur lokal, nicht auf GitHub.

**\#\#\# Wie der Nebenrechner gearbeitet hat (Methodik, damit du die Beweiskette nachvollziehst)**  
1\. Projekt lokalisiert (\`.personal-jarvis\`, nicht der leere Desktop-Ordner \`PersonalJarvisGitHUb\`).  
2\. Wake-Architektur gelesen: \`jarvis/speech/wake\_phrase.py\` (\`resolve\_wake\_plan\`),  
   \`jarvis/speech/wake\_constants.py\`, \`jarvis/plugins/wake/vosk\_kws\_provider.py\`,  
   \`jarvis/plugins/wake/openwakeword\_provider.py\`, \`jarvis/speech/rolling\_whisper\_wake.py\`.  
3\. **\*\*Laufzeit-Log\*\*** \`data/jarvis\_desktop.log\` als primäre Wahrheitsquelle ausgewertet.  
4\. Zwei **\*\*read-only Vosk-Probes\*\*** gefahren (mit \`SetLogLevel(0)\`, das der Produktivcode  
   normalerweise per \`SetLogLevel(-1)\` unterdrückt), um OOV-Warnungen sichtbar zu machen.

**\---**

**\#\# 1\. Symptom**

\- Wake-Word \`"Hey Billionar"\` löste **\*\*nie\*\*** aus.  
\- Nach Änderung auf \`"Hey Ruben"\` (bzw. Namen allgemein) löst es **\*\*ebenfalls nie\*\*** aus.  
\- Ziel des Maintainers: **\*\*Es soll mit JEDEM Wort auf JEDEM Rechner klappen.\*\***

**\---**

**\#\# 2\. Zwei verschiedene Ursachen (beide bewiesen)**

**\#\#\# Ursache A — „Billionar": Out-of-Vocabulary (OOV)**  
Der Vosk-Provider baut die Grammatik aus der Phrase  
(\`jarvis/plugins/wake/vosk\_kws\_provider.py\`, \~Zeile 205):  
\`\`\`python  
grammar \= json.dumps(\[self.\_phrase.lower(), "\[unk\]"\])   \# \-\> \["hey billionar", "\[unk\]"\]  
\`\`\`  
Vosk kann **\*\*nur Wörter erkennen, die im Lexikon des Modells stehen.\*\*** \`billionar\` ist kein  
englisches Wort. **\*\*Read-only-Probe gegen das echte Modell:\*\***  
\`\`\`  
'hey billionar'   \-\>  WARNING: Ignoring word missing in vocabulary: 'billionar'  
                      Grammatik \= 3 states / 4 arcs   (nur "hey" bleibt übrig)  
'hey billionaire' \-\>  (keine Warnung)  4 states / 7 arcs   OK im Vokabular  
\`\`\`  
→ Das Wort wird **\*\*still verworfen\*\***, die Phrase kann nie gehört werden, der Wake feuert nie.  
Weil der Code \`SetLogLevel(-1)\` setzt, steht die Warnung **\*\*nicht\*\*** im App-Log — es wirkt  
„gesund", ist aber taub.

**\#\#\# Ursache B — „Ruben": akustisches Sprach-Mismatch (der eigentliche Kern)**  
\`ruben\` **\*\*ist\*\*** im Vokabular (kein OOV — auch getestet). Die Grammatik-Stufe **\*\*feuert sogar\*\***.  
Aber die **\*\*Verifikationsstufe lehnt jedes Mal ab.\*\*** Beweis aus \`data/jarvis\_desktop.log\`:  
\`\`\`  
vosk-kws: verify SUPPRESSED — free ear heard 'hey of whom'  vs phrase 'Hey Ruben'  
vosk-kws: verify SUPPRESSED — free ear heard 'hey will be'  vs phrase 'Hey Ruben'  
vosk-kws: verify SUPPRESSED — free ear heard 'hey all been' vs phrase 'Hey Ruben'  
vosk-kws: verify SUPPRESSED — re-score did not re-hear 'Hey Ruben' (heard 'hey \[unk\]')  
vosk-kws: verify SUPPRESSED — free ear heard 'can a woman' vs phrase 'Hey Ruben'  
\`\`\`  
**\*\*Grund:\*\*** Du sprichst „Hey Ruben" **\*\*deutsch\*\***. Das geladene Vosk-Modell ist aber  
**\*\*englisch\*\*** (\`vosk-model-small-en-us-0.15\`). Ein Vosk-Modell ist akustisch  
**\*\*sprachspezifisch\*\*** — das englische Modell kann dein deutsch gesprochenes „Ruben" nicht auf  
das englische Lautbild abbilden (es hört „of whom / a woman"). Die Verify-Stufe schützt  
korrekt vor Fehlauslösung und lehnt ab → Wake feuert nie.

**\*\*Warum überhaupt das englische Modell?\*\*** Config hat \`stt.language \= "auto"\` (Default). Bei  
„auto" nimmt \`resolve\_vosk\_model\_path\` einfach das **\*\*erstinstallierte\*\*** Modell — hier das  
englische (das einzige installierte). Es gibt \*\*keine Prüfung, ob die Modellsprache zum  
Sprecher passt.\*\* Das ist der zentrale Logikfehler.

**\#\#\# Das gemeinsame Grundprinzip (unbedingt verstehen)**  
Ein **\*\*Vosk-KWS-Modell ist doppelt begrenzt:\*\***  
1\. **\*\*Geschlossenes Vokabular\*\*** → erfundene/abgekürzte Wörter scheitern (Ursache A).  
2\. **\*\*Sprachspezifische Akustik\*\*** → ein Wort in anderer Sprache/Aussprache scheitert, selbst  
   wenn es im Vokabular steht (Ursache B).

→ **\*\*Vosk kann prinzipiell NICHT „jedes Wort in jeder Sprache".\*\*** Der einzige Pfad, der das  
kann, ist **\*\*mehrsprachiges, offenes-Vokabular Whisper\*\*** (\`stt\_match\` / \`RollingWhisperWake\`).

**\---**

**\#\# 3\. Warum der Fix auf diesem Rechner sofort greifen würde**  
Auf dem Nebenrechner ist **\*\*mehrsprachiges faster-whisper voll installiert\*\***  
(\`faster-whisper-base\`, \`-small\`, \`distil-large-v3\` im HF-Cache). Der \`stt\_match\`\-Pfad  
transkribiert dein deutsches „Hey Ruben" korrekt. Der Bug ist nur, dass \`resolve\_wake\_plan\`  
**\*\*Vosk bevorzugt\*\***, sobald irgendein Vosk-Modell existiert, und **\*\*nie\*\*** auf den universellen  
Whisper-Pfad zurückfällt — selbst wenn die Vosk-Sprache nicht passt.

**\---**

**\#\# 4\. DER FIX**

**\#\#\# Kern-Wahrheit in einem Satz**  
\> \*\*Vertraue Vosk nur, wenn seine Sprache nachweislich zum Sprecher passt — sonst nimm das  
\> mehrsprachige, offene Whisper (\`stt\_match\`).\*\*

Das löst **\*\*beide\*\*** Ursachen auf jedem Rechner mit lokalem Whisper automatisch (denn dort wird  
gar kein Vosk mehr für unpassende Fälle gewählt).

**\---**

**\#\#\# Patch 1 — Sprach-bewusstes Routing (PFLICHT)**

**\*\*Datei:\*\*** \`jarvis/speech/wake\_phrase.py\`  
**\*\*Funktion:\*\*** \`resolve\_wake\_plan\`, **\*\*Schritt 2\*\*** (im Original Zeilen **\*\*416–421\*\***).

Ersetze diesen Block …  
\`\`\`python  
    if phrase and engine\_pref in ("auto", "vosk\_kws"):  
        if vosk\_available is None:  
            import importlib.util as \_ilu

            vosk\_available \= \_ilu.find\_spec("vosk") is not None  
        vosk\_model \= resolve\_vosk\_model\_path(language) if vosk\_available else None  
        if vosk\_model is not None and (  
            engine\_pref \== "vosk\_kws"  
            or not custom\_path  
            or custom\_stale  
            or custom\_missing  
        ):  
\`\`\`  
… durch diesen (alles darunter — der \`return WakeWordPlan(engine="vosk\_kws", …)\` — bleibt  
UNVERÄNDERT):  
\`\`\`python  
    \# A Vosk model is language-SPECIFIC acoustically: an English model cannot  
    \# hear a German-pronounced name even when the word IS in its lexicon (live  
    \# 2026-07-09: 'Hey Ruben' spoken de on the en model free-decoded to  
    \# 'hey of whom'/'a woman' and EVERY verify suppressed — a silent dead  
    \# listener). So vosk\_kws is trusted only when its language provably matches  
    \# the speaker: (a) engine explicitly forced, or (b) a CONCRETE language is  
    \# pinned and a model for THAT language is installed. Under an ambiguous  
    \# "auto" language we do NOT gamble on the first-installed model — we prefer  
    \# the multilingual, open-vocabulary stt\_match path whenever local Whisper is  
    \# available (it transcribes ANY word in ANY language). Vosk stays the  
    \# best-effort fallback only on a box with NO local Whisper.  
    lang\_norm \= (language or "auto").strip().lower().split("-")\[0\]  
    lang\_is\_concrete \= bool(lang\_norm) and lang\_norm \!= "auto"  
    vosk\_model \= None  
    if phrase and engine\_pref in ("auto", "vosk\_kws"):  
        if vosk\_available is None:  
            import importlib.util as \_ilu

            vosk\_available \= \_ilu.find\_spec("vosk") is not None  
        if vosk\_available:  
            if engine\_pref \== "vosk\_kws":  
                \# Explicit force: honour the user's choice, any installed model.  
                vosk\_model \= resolve\_vosk\_model\_path(language)  
            elif lang\_is\_concrete:  
                \# auto engine \+ a pinned language: trust vosk only for a model  
                \# in exactly that language (never a mismatched fallback).  
                vosk\_model \= resolve\_vosk\_model\_path(lang\_norm)  
            elif not local\_whisper\_available:  
                \# auto engine \+ ambiguous language \+ NO multilingual Whisper:  
                \# vosk (first-installed) is the only local option — best effort.  
                vosk\_model \= resolve\_vosk\_model\_path(language)  
            \# else: auto \+ ambiguous language \+ Whisper present \-\> fall through  
            \# to the multilingual stt\_match path below (the universal answer).  
        if vosk\_model is not None and (  
            engine\_pref \== "vosk\_kws"  
            or not custom\_path  
            or custom\_stale  
            or custom\_missing  
        ):  
\`\`\`

**\*\*Wirkung:\*\*** \`engine="auto"\` \+ \`stt.language="auto"\` \+ Whisper installiert → \`vosk\_model\`  
bleibt \`None\` → Durchfall auf \`stt\_match\` → mehrsprachiges Whisper hört „Hey Ruben" (und  
„billionar", da offenes Vokabular). Schneller Vosk-Pfad bleibt erhalten für explizites  
\`engine="vosk\_kws"\` oder eine konkret gepinnte, passende Sprache (z. B. \`language="de"\` mit  
installiertem de-Modell).

\> **\*\*Alternative, falls du Universalität über Latenz stellst:\*\*** einfach \`stt\_match\` immer  
\> bevorzugen, sobald \`local\_whisper\_available\` — noch robuster, aber Vosk-Geschwindigkeit  
\> geht verloren. Die obige sprach-bewusste Variante ist der chirurgische Kompromiss.

**\---**

**\#\#\# Patch 2 — OOV-Guard (OPTIONAL, nur für Rechner OHNE lokales Whisper)**

Auf einem Headless-/VPS-Rechner ohne Whisper bleibt Vosk der einzige Pfad — dort verschluckt  
Vosk ein Nicht-Vokabular-Wort weiterhin still. Guard dagegen.

**\*\*Datei:\*\*** \`jarvis/plugins/wake/vosk\_kws\_provider.py\` — neue Funktion (nutzt genau die  
stderr-Capture-Technik, die auf dem Nebenrechner erfolgreich getestet wurde; portabel auf  
Windows \+ POSIX):  
\`\`\`python  
def vosk\_model\_supports\_phrase(model\_path: str, phrase: str) \-\> bool:  
    """True when every core word of \`\`phrase\`\` exists in the model lexicon.

    Vosk drops out-of-vocabulary grammar words with a stderr warning and  
    silently builds a grammar without them (live 2026-07-08: 'Hey Billionar'  
    \-\> "Ignoring word missing in vocabulary: 'billionar'" \-\> the phrase could  
    never be heard). The warning is our only signal (no readable word list in  
    the small models), so capture it at the OS fd level — portable on Windows  
    and POSIX.  
    """  
    import json  
    import os  
    import tempfile

    from vosk import KaldiRecognizer, Model, SetLogLevel

    from jarvis.speech.wake\_constants import phrase\_core\_for\_match

    core \= phrase\_core\_for\_match(phrase)  
    if not core:  
        return False  
    SetLogLevel(0)  
    tmp \= tempfile.TemporaryFile(mode="w+")  
    old \= os.dup(2)  
    os.dup2(tmp.fileno(), 2\)  
    try:  
        KaldiRecognizer(Model(model\_path), 16\_000, json.dumps(\[phrase.lower(), "\[unk\]"\]))  
    finally:  
        os.dup2(old, 2\)  
        os.close(old)  
        SetLogLevel(-1)  
    tmp.seek(0)  
    return "missing in vocabulary" not in tmp.read().lower()  
\`\`\`  
\*\*Aufruf:\*\* in \`resolve\_wake\_plan\` unmittelbar bevor der \`vosk\_kws\`-Plan zurückgegeben wird —  
nur wenn Vosk wirklich gewählt wurde. Bei \`not supported\` → \`vosk\_model \= None\` und ehrlich in  
Schritt 4 degradieren (\`wake\_available=False\`) mit klarer Meldung: „Wort X ist im Sprachmodell  
nicht vorhanden — wähle ein echtes Wort dieser Sprache, installiere das Sprachpaket, oder  
trainiere ein custom .onnx."

\> ⚠️ \*\*Boot-Budget (AP-26):\*\* Diese Prüfung lädt das Vosk-Modell (\~1,5 s). \*\*Nicht\*\* in den  
\> \`\_run\_backend\`-Boot-Pfad legen — nur im Hintergrund-Wake-Build ausführen, wo das Modell  
\> ohnehin geladen wird.

\---

\#\#\# Patch 3 — Laufzeit-Selbstheilung (OPTIONAL, Kür)

Feuert die Vosk-Grammatik, wird aber N-mal in Folge „SUPPRESSED" (genau die „Ruben"-Signatur),  
schalte die Session live auf \`stt\_match\` um. Skizze: in \`VoskKwsProvider.detect()\` die  
\`\_stat\_suppressed\_confirm\`-Serie beobachten; bei ≥ 4 Suppressions ohne Fire in \~30 s einmalig  
ein Signal/Event werfen → Pipeline baut mit \`engine="stt\_match"\` neu auf. Größerer Eingriff —  
nur bei Bedarf an maximaler Robustheit.

\---

\#\# 5\. Tests (Repo-Konvention: \`tests/unit/speech/\`)

In \`test\_wake\_plan\_vosk.py\` bzw. \`test\_pipeline\_wake\_plan.py\` ergänzen:  
\`\`\`python  
def test\_auto\_language\_with\_whisper\_prefers\_multilingual\_stt\_over\_mismatched\_vosk():  
    \# only an EN vosk model installed, user language "auto", whisper present  
    cfg \= \_wwcfg(phrase="Hey Ruben", engine="auto")  
    plan \= resolve\_wake\_plan(cfg, local\_whisper\_available=True,  
                             language="auto", vosk\_available=True)  
    assert plan.engine \== "stt\_match"          \# NOT vosk\_kws (would be deaf to de)  
    assert plan.wake\_available

def test\_concrete\_matching\_language\_still\_uses\_fast\_vosk():  
    \# with a de vosk model present \+ language pinned to "de"  
    plan \= resolve\_wake\_plan(\_wwcfg("Hey Ruben", "auto"),  
                             local\_whisper\_available=True, language="de",  
                             vosk\_available=True)  
    assert plan.engine \== "vosk\_kws"

def test\_headless\_no\_whisper\_falls\_back\_to\_vosk\_best\_effort():  
    plan \= resolve\_wake\_plan(\_wwcfg("Hey Ruben", "auto"),  
                             local\_whisper\_available=False, language="auto",  
                             vosk\_available=True)  
    assert plan.engine \== "vosk\_kws"  
\`\`\`  
Für Patch 2 zusätzlich: \`vosk\_model\_supports\_phrase(model, "Hey Billionar") is False\` und  
\`… "Hey Billionaire") is True\`.

\---

\#\# 6\. Anwenden, verifizieren, pushen (auf dem Hauptrechner)

\`\`\`bash  
\# 0\) richtige Python-Quelle sicherstellen (BUG-006/014)  
pwsh scripts/preflight.ps1  
python \-c "import jarvis; print(jarvis.\_\_file\_\_)"

\# 1\) Patch 1 (+ optional 2/3) einspielen, dann Qualitätsgates  
ruff check jarvis/ && ruff format jarvis/ && mypy jarvis/  
pytest tests/unit/speech/ \-v          \# inkl. der neuen Tests

\# 2\) Entry-points/editable-install aktivieren  
pip install \-e . \--no-deps

\# 3\) App NICHT via Stop-Process neu starten, sondern:  
\#    POST /api/settings/restart-app  
\`\`\`

\*\*Erfolg verifizieren im Log\*\* (\`data/jarvis\_desktop.log\`):  
\- \`Wake-word plan: engine=stt\_match … phrase='Hey Ruben'\`  
\- \`… WHISPER-WAKE=on\`  
\- beim Sprechen: ein Wake-Treffer \*\*statt\*\* „verify SUPPRESSED".

\---

\#\# 7\. GitHub-Push (nur auf ausdrückliche Freigabe des Maintainers)

Öffentlicher Repo: \`https://github.com/PersonalJarvis/PersonalJarvis\`. \*\*Nur\*\* über die  
§2-Privacy-Gate (Discreet-Snapshot, PII-Scrub, Sub-Agent-Review, Human-Review) — \*\*nie\*\* roher  
Push, \`save-to-github\`/\`github-version\` sind verboten. Commit-Vorschlag:  
\`\`\`  
fix(wake): route arbitrary/cross-language wake words to multilingual stt\_match

An English Vosk model cannot acoustically hear a German-spoken name even when  
the word is in its lexicon ('Hey Ruben' \-\> free-decoded 'hey of whom', every  
verify suppressed \-\> silent dead listener). resolve\_wake\_plan now trusts  
vosk\_kws only when its language provably matches the speaker and prefers the  
multilingual open-vocabulary Whisper path under ambiguous language. Also guards  
against out-of-vocabulary words silently dropped from the Vosk grammar.  
Fixes the silent-no-fire class for any word on any \[full\]-install machine  
(AP-27, §3).  
\`\`\`

\---

\#\# 8\. Sofort-Workaround (falls du es SOFORT laufen lassen willst, ohne Code)

Rein per Config in \`jarvis.toml\` (reversibel):  
\`\`\`toml  
\[trigger.wake\_word\]  
phrase  \= "Hey Ruben"  
engine  \= "stt\_match"     \# statt "auto" \-\> umgeht Vosk, nutzt mehrsprachiges Whisper  
sensitivity \= 0.5

\[stt\]  
language \= "de"           \# pinnt die Wake-Transkription auf Deutsch  
\`\`\`  
Danach App-Neustart via \`POST /api/settings/restart-app\`. Das ist nur ein Pflaster — der  
richtige Fix ist Patch 1, damit es \*\*für alle Nutzer und ohne Handarbeit\*\* stimmt.

\---

\#\# 9\. Für-maximale-Zuverlässigkeit-bei-EINEM-Wort (Ausblick)  
Der stt\_match/Whisper-Pfad ist universell, aber bei „harten" Namen nicht 100 %. Für ein  
felsenfestes einzelnes Wake-Word ist ein \*\*trainiertes openWakeWord \`custom\_onnx\`-Modell\*\*  
(neuronales KWS, AP-25) die Königslösung — braucht aber Trainingsaufwand. Für „jedes Wort  
sofort" bleibt Whisper der richtige Default.

\---

\#\#\# Betroffene Dateien (Kurzliste)  
\- \`jarvis/speech/wake\_phrase.py\` — \*\*Patch 1\*\* (\`resolve\_wake\_plan\`, Schritt 2\)  
\- \`jarvis/plugins/wake/vosk\_kws\_provider.py\` — \*\*Patch 2\*\* (neue \`vosk\_model\_supports\_phrase\`) \+ Aufruf, optional \*\*Patch 3\*\*  
\- \`tests/unit/speech/test\_wake\_plan\_vosk.py\` / \`test\_pipeline\_wake\_plan.py\` — neue Tests  
\- (Referenz, nicht ändern) \`jarvis/speech/wake\_constants.py\`, \`jarvis/speech/rolling\_whisper\_wake.py\`

\*Erstellt von einem Read-only-Diagnose-Agenten auf dem Nebenrechner. Code-Stand: v1.0.4 @ \`1693874\`. Es wurde nichts verändert.\*

