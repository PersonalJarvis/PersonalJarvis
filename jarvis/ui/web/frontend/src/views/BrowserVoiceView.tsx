/**
 * BrowserVoiceView — minimal UI for the B2 browser-microphone voice bridge.
 *
 * A start/stop control over the `useBrowserVoice` hook plus a live transcript
 * list. Browser-only at runtime (AudioWorklet + Web Audio); not unit-tested in
 * jsdom. i18n keys carry English defaultValue fallbacks so the view renders
 * before the `browser_voice.*` locale keys are wired in.
 */
import { useState } from "react";

import { useT } from "@/i18n";
import { useBrowserVoice } from "@/hooks/useBrowserVoice";

export default function BrowserVoiceView() {
  const t = useT();
  // useT returns the key itself when a translation is missing; fall back to the
  // English source until the browser_voice.* keys are wired into the locales.
  const tr = (key: string, fallback: string) => {
    const value = t(key);
    return value === key ? fallback : value;
  };
  const [transcripts, setTranscripts] = useState<string[]>([]);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { active, start, stop } = useBrowserVoice({
    onTranscript: (text, isFinal) => {
      if (isFinal && text) setTranscripts((prev) => [...prev, text]);
    },
    onTtsStart: () => setSpeaking(true),
    onTtsEnd: () => setSpeaking(false),
    onError: (message) => setError(message),
  });

  const toggle = () => {
    setError(null);
    if (active) {
      stop();
    } else {
      void start();
    }
  };

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold">{tr("browser_voice.title", "Browser Voice")}</h1>
      <p className="max-w-prose text-sm text-muted-foreground">
        {tr(
          "browser_voice.subtitle",
          "Talk to Jarvis using your browser's microphone and speakers — no desktop install. Requires a secure context (localhost or https).",
        )}
      </p>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggle}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
        >
          {active ? tr("browser_voice.stop", "Stop") : tr("browser_voice.start", "Start voice")}
        </button>
        {active && (
          <span className="text-xs text-muted-foreground">
            {speaking
              ? tr("browser_voice.speaking", "Speaking…")
              : tr("browser_voice.listening", "Listening…")}
          </span>
        )}
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <ul className="flex flex-col gap-1 text-sm">
        {transcripts.map((text, i) => (
          <li key={`${i}-${text}`} className="rounded bg-muted/40 px-3 py-1.5">
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}
