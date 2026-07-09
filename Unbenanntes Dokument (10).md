AUFGABE: Hol den bereits nach GitHub gepushten Wake-Word-Fix in unser lokales  
PersonalJarvis-Repo auf DIESEM Rechner und aktiviere ihn. Ändere nur, was nötig  
ist; arbeite hunk-sicher (der Arbeitsbaum ist geteilt — evtl. laufen parallele  
Sessions). NIEMALS \`git add \-A\`/\`git add .\`, NIEMALS \`git push \--force\`, und  
\`jarvis.toml\` / \`.env\` / \`data/\` NICHT anfassen (das ist lokale Config/Secrets).

KONTEXT  
\- Öffentliches Repo (origin): https://github.com/PersonalJarvis/PersonalJarvis.git  
\- Fertiger Branch mit dem Fix: \`fix/wake-language-aware\` (HEAD 7f9794c),  
  4 Commits auf Basis 1693874:  
    354f4fb  language-aware CPU wake routing \+ auto-provision \+ self-test  
    d7d9610  prominente, eindeutige Sprach-Erklärung im UI  
    127f3b1  gebrandetes Dropdown, "Auto" entfernt  
    7f9794c  live-Umschaltung bei Sprachwechsel  
\- Inhalt: Das Wake-Word war für Nicht-Englisch-Sprecher lautlos taub (falsches,  
  sprach-fremdes Vosk-Modell). Fix \= sprach-bewusstes Routing \+ Auto-Download des  
  passenden Modells \+ UI-Sprachwahl (schwarz/gelb, ohne "Auto") \+ CPU-Pinning.  
  Geänderte Dateien u.a.: jarvis/speech/wake\_phrase.py, wake\_model\_fetch.py,  
  jarvis/plugins/wake/vosk\_kws\_provider.py, jarvis/plugins/stt/\_\_init\_\_.py,  
  jarvis/core/config.py, jarvis/ui/desktop\_app.py, jarvis/ui/web/settings\_routes.py,  
  jarvis/ui/web/frontend/src/views/SettingsView.tsx \+ i18n en/de/es \+  
  tests/unit/speech/test\_wake\_language\_aware.py \+ gebautes dist/.

SCHRITTE  
1\. Finde das lokale PersonalJarvis-Repo (wahrscheinlich unter  
   \`%USERPROFILE%\\Desktop\\Personal Jarvis\` oder dem managed install  
   \`%USERPROFILE%\\.personal-jarvis\`). Prüfe: \`git remote \-v\` muss auf  
   PersonalJarvis/PersonalJarvis zeigen. Wechsle in dieses Verzeichnis.  
2\. Sichern: \`git status \-sb\`. Falls uncommittete Änderungen da sind, die NICHT  
   zu diesem Fix gehören → \`git stash push \-u \-m pre-wakefix\`. Merke dir, ob  
   gestasht wurde.  
3\. Nur die Änderungen holen (inkrementell, NICHT das ganze Repo):  
     git fetch origin fix/wake-language-aware  
4\. In den aktuellen Branch mergen (die Commits betreffen andere Dateien als der  
   Rest, daher konfliktfrei):  
     git merge \--no-ff FETCH\_HEAD \-m "merge: wake-word language-aware fix"  
   Bei Konflikt: pausieren, den Konflikt sauber auflösen (unsere Wake-Dateien  
   gewinnen inhaltlich), dann \`git merge \--continue\`. Nichts erzwingen.  
5\. Falls in Schritt 2 gestasht: \`git stash pop\` (Konflikte sauber lösen).  
6\. Reaktivieren:  
     pwsh scripts/preflight.ps1        \# BUG-006/014, Working-Tree/Editable-Install prüfen  
     python \-c "import jarvis; print(jarvis.\_\_file\_\_)"   \# muss auf DIESES Repo zeigen  
     pip install \-e . \--no-deps        \# Entry-points aktiv (schadet nicht)  
   Das Frontend ist als gebautes dist/ enthalten — kein npm-Build nötig.  
7\. App neu starten NICHT per Stop-Process, sondern (wenn die App läuft):  
     POST http://127.0.0.1:47821/api/settings/restart-app  
   (falls Port abweicht: den lauschenden Backend-Port des laufenden  
   PersonalJarvis/pythonw-Prozesses ermitteln). Läuft sie nicht, normal starten.

VERIFIKATION (muss alles zutreffen)  
\- Marker im Code:  
    grep \-c lang\_is\_concrete jarvis/speech/wake\_phrase.py        \# \>= 2  
    grep \-c vosk\_model\_supports\_phrase jarvis/plugins/wake/vosk\_kws\_provider.py  \# \>= 2  
    python \-c "from jarvis.core.config import STTConfig; print(STTConfig().wake\_high\_accuracy)"  \# False  
\- Tests:  
    python \-m pytest tests/unit/speech/test\_wake\_language\_aware.py \-q   \# alle grün  
\- Funktionaler Trace (mit ECHTER Config des Nutzers):  
    python \-c "from jarvis.core.config import load\_config; from jarvis.speech.wake\_model\_fetch import resolve\_wake\_language; from jarvis.speech.wake\_phrase import resolve\_wake\_plan; import importlib.util as i; c=load\_config(); l=resolve\_wake\_language(c); print('lang=',l); p=resolve\_wake\_plan(c.trigger.wake\_word, local\_whisper\_available=i.find\_spec('faster\_whisper') is not None, language=l); print('engine=',p.engine,'wake\_available=',p.wake\_available,'msg=',p.message\[:100\])"  
  Erwartung: engine ist \`vosk\_kws\` (mit sprach-passendem Modell) ODER \`stt\_match\`  
  (mehrsprachiges Whisper); wake\_available=True. NIE ein sprach-fremdes Vosk-Modell.

RUNTIME-HINWEIS (WICHTIG, sonst wirkt es "kaputt")  
\- Der Fix wählt das Modell nach der Sprache, die der NUTZER SPRICHT. Ist im UI  
  noch keine konkrete Sprache gesetzt (stt.language="auto" \+ ui.language="en"),  
  wird Englisch angenommen. Also: in Settings → Wake Word das Sprach-Dropdown auf  
  die tatsächlich gesprochene Sprache stellen (z.B. Deutsch). Fehlt das passende  
  Vosk-Modell, lädt es der Fix beim Sprachwechsel/Boot automatisch nach; solange  
  greift mehrsprachiges Whisper. Der "Wake-Word testen"-Button zeigt den Status.  
\- Getrennt davon: Wenn nach dem Wecken die Cloud-Antwort ausbleibt, prüfe das  
  STT/Brain-Guthaben (z.B. OpenRouter 402\) — das ist NICHT der Wake-Word-Fix.

ABSCHLUSS  
\- Melde: gemergter Commit-SHA, Ergebnis der Verifikation, ob gestasht/gepoppt  
  wurde. NICHT nach GitHub pushen (nur lokal integrieren) außer der Nutzer sagt  
  es ausdrücklich.

