/**
 * Robust-Clipboard + Download-Helper.
 *
 * Hintergrund: pywebview verwendet auf Windows die WebView2-Engine
 * (Edge Chromium). Deren ``navigator.clipboard.writeText`` hat fuer
 * **Multi-Line-Strings** (z.B. JSON mit ``indent=2``) ein bekanntes
 * Quirk-Verhalten — es kann passieren, dass nur die erste Zeile (= ``{``)
 * im System-Clipboard landet. Markdown/Plain-Text trifft das seltener,
 * weil deren Header die erste Zeile mit weiteren Zeichen fuellen.
 *
 * Loesung: robustCopy() versucht erst die moderne Clipboard-API. Bei
 * Failure ODER bei verdaechtig kurzem Output (Multi-Line-Truncation)
 * faellt es auf den klassischen ``document.execCommand('copy')``-Pfad
 * zurueck — der via hidden ``textarea`` + Selection arbeitet und in
 * WebView2 zuverlaessig den vollen Inhalt schreibt.
 */

/**
 * Kopiert einen String robust ins System-Clipboard.
 *
 * Returns true wenn erfolgreich (oder Fallback-Pfad genutzt wurde),
 * false wenn beide Pfade fehlschlugen.
 */
export async function robustCopy(text: string): Promise<boolean> {
  // Pfad A — moderne Clipboard-API. Schnell und sicher fuer kurze Texte.
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      // WebView2-Quirk: writeText() resolve()d auch dann, wenn nur ein
      // Teilstring uebernommen wurde. Wir koennen das Ergebnis nicht
      // zuverlaessig zurueck-lesen (readText braucht Permissions, die
      // pywebview nicht standardmaessig grantet). Daher: bei Multi-Line
      // immer auch den Fallback ausfuehren — er ueberschreibt ggf.
      // den korrupten Eintrag mit dem vollstaendigen Text.
      if (text.includes("\n")) {
        return execCommandCopy(text);
      }
      return true;
    }
  } catch {
    // Pfad A fehlgeschlagen — durchfallen zu Fallback.
  }
  // Pfad B — klassischer Fallback via hidden textarea.
  return execCommandCopy(text);
}

function execCommandCopy(text: string): boolean {
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  // Off-screen, damit kein Layout-Shift / Focus-Verlust beim User
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.top = "0";
  ta.style.opacity = "0";
  ta.setAttribute("readonly", "");
  document.body.appendChild(ta);
  // Wichtig: focus + select muessen passieren bevor execCommand laeuft,
  // sonst hat WebView2 nichts zum Kopieren.
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

/**
 * Triggert einen Datei-Download via Blob + temporaeres ``<a download>``.
 *
 * Funktioniert in WebView2 (pywebview) — dort wird die Datei in den
 * Default-Downloads-Ordner gelegt (Edge-Verhalten). Im Vite-Dev-Server
 * popt der Browser den Save-Dialog.
 */
export function downloadAs(
  filename: string,
  content: string,
  mime = "text/plain;charset=utf-8",
): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    // Off-screen halten, damit kein visueller Flicker beim Click.
    a.style.position = "fixed";
    a.style.left = "-9999px";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    // ObjectURL freigeben, sobald der Download-Stream Zeit hatte zu starten.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/**
 * Erzeugt einen filesystem-tauglichen Filename fuer eine Voice-Session.
 *
 * Pattern: ``voice-session-YYYY-MM-DD_HH-mm-{slug}.{ext}``
 *  - YYYY-MM-DD_HH-mm aus session.started_ms (LocalTime)
 *  - slug aus den ersten 3-4 Woertern der ersten User-Utterance falls
 *    vorhanden, sonst der ersten 8 Zeichen der session_id
 *
 * Filesystem-Sanitizing: nur ``[a-z0-9-]`` plus Bindestriche.
 */
export function buildSessionFilename(
  session: { id: string; started_ms: number },
  preview: string,
  format: "markdown" | "plain" | "json",
): string {
  const ext = format === "markdown" ? "md" : format === "plain" ? "txt" : "json";
  const dt = new Date(session.started_ms);
  const pad = (n: number): string => String(n).padStart(2, "0");
  const stamp = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}_${pad(dt.getHours())}-${pad(dt.getMinutes())}`;
  const slug = slugify(preview) || session.id.slice(0, 8);
  return `voice-session-${stamp}-${slug}.${ext}`;
}

function slugify(s: string): string {
  if (!s) return "";
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // diacritics weg
    .replace(/[^a-z0-9\s-]/g, " ") // alles non-alnum zu Space
    .trim()
    .split(/\s+/)
    .slice(0, 4) // max 4 Woerter
    .join("-")
    .slice(0, 40); // hard cap
}

/**
 * Mime-Type fuer ein Export-Format.
 */
export function mimeFor(format: "markdown" | "plain" | "json"): string {
  if (format === "json") return "application/json;charset=utf-8";
  if (format === "markdown") return "text/markdown;charset=utf-8";
  return "text/plain;charset=utf-8";
}
