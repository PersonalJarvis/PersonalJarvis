import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import {
  fetchRealtimeVoicePreview,
  getRealtimeOptions,
  saveRealtimeOptions,
  type RealtimeOptionInfo,
} from "@/hooks/useProviders";
import { PreviewButton } from "@/components/OpenRouterTtsVoicePicker";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT, useUiLanguage } from "@/i18n";

// "" always means "use the provider's own default" — mirrors the backend
// contract (RealtimeOptionsResponse.current_model/current_voice empty when
// nothing is pinned, RealtimeOptionsBody accepting "" to explicitly clear).
const PROVIDER_DEFAULT = "";

/**
 * Per-realtime-provider MODEL + VOICE picker.
 *
 * Unlike every other tier, a realtime session needs BOTH a model and a voice
 * pinned per provider, so this is a small dedicated control against the
 * dedicated `GET/PUT /api/providers/{id}/realtime-options` endpoint rather
 * than the shared search-heavy `BrainModelSelector`.
 *
 * The model stays a plain `<select>` (a handful of curated entries). The
 * voice is a richer expanding picker with a per-voice audio preview
 * (`POST /api/providers/{id}/realtime-voice-preview`) so a voice can be
 * HEARD before it is pinned — auditioning must not write config or restart a
 * live realtime session; only clicking a name saves.
 *
 * Renders only inside a realtime provider card (`tier === "realtime"`), gated
 * on the card already having a stored credential — see `ApiKeysView.tsx`.
 */
export function RealtimeOptionsControl({
  providerId,
  healthActive = false,
}: {
  providerId: string;
  healthActive?: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const [models, setModels] = useState<RealtimeOptionInfo[]>([]);
  const [voices, setVoices] = useState<RealtimeOptionInfo[]>([]);
  const [model, setModel] = useState<string>(PROVIDER_DEFAULT);
  const [voice, setVoice] = useState<string>(PROVIDER_DEFAULT);
  const [loading, setLoading] = useState(true);
  const [savingField, setSavingField] = useState<"model" | "voice" | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    void getRealtimeOptions(providerId)
      .then((r) => {
        if (!alive) return;
        // Defensive: an unexpected/malformed response (e.g. a catch-all test
        // fixture, or a future backend shape change) must degrade to empty
        // lists rather than crash the card's render.
        setModels(Array.isArray(r?.models) ? r.models : []);
        setVoices(Array.isArray(r?.voices) ? r.voices : []);
        setModel(r?.current_model || PROVIDER_DEFAULT);
        setVoice(r?.current_voice || PROVIDER_DEFAULT);
      })
      .catch(() => {
        // Best-effort — the row degrades to empty dropdowns rather than
        // breaking the card.
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [providerId]);

  async function handleChange(field: "model" | "voice", next: string) {
    const prev = field === "model" ? model : voice;
    if (field === "model") setModel(next);
    else setVoice(next);
    setSavingField(field);
    if (healthActive) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-selection-pending", {
          detail: { section: "realtime", provider: providerId },
        }),
      );
    }
    try {
      await saveRealtimeOptions(
        providerId,
        field === "model" ? { model: next } : { voice: next },
      );
      if (healthActive) {
        window.dispatchEvent(
          new CustomEvent("jarvis:provider-config-changed", {
            detail: { section: "realtime", provider: providerId },
          }),
        );
      }
    } catch (e) {
      // Roll back the optimistic pick on failure.
      if (field === "model") setModel(prev);
      else setVoice(prev);
      if (healthActive) {
        window.dispatchEvent(
          new CustomEvent("jarvis:provider-switch-failed", {
            detail: { section: "realtime", provider: providerId },
          }),
        );
      }
      pushToast("error", (e as Error).message);
    } finally {
      setSavingField(null);
    }
  }

  if (loading) return null;

  return (
    <div
      className="space-y-1.5"
      // A click into the selects must not bubble to the card's activate
      // handler (it only filters input/button/a/label, not select).
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <RealtimeSelectRow
        label={t("apikeys_view.realtime_model_label")}
        value={model}
        options={models}
        saving={savingField === "model"}
        onChange={(next) => void handleChange("model", next)}
      />
      <RealtimeVoiceRow
        providerId={providerId}
        label={t("apikeys_view.realtime_voice_label")}
        value={voice}
        model={model}
        options={voices}
        saving={savingField === "voice"}
        onChange={(next) => void handleChange("voice", next)}
      />
    </div>
  );
}

function RealtimeSelectRow({
  label,
  value,
  options,
  saving,
  onChange,
}: {
  label: string;
  value: string;
  options: RealtimeOptionInfo[];
  saving: boolean;
  onChange: (value: string) => void;
}) {
  const t = useT();
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <select
        aria-label={label}
        value={value}
        disabled={saving}
        onChange={(e) => onChange(e.target.value)}
        className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 text-xs disabled:opacity-60"
      >
        <option value={PROVIDER_DEFAULT}>
          {t("apikeys_view.realtime_provider_default")}
        </option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
      {saving && (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
      )}
    </div>
  );
}

/**
 * Voice row: current pick + preview button, expanding into the full voice
 * list where EVERY voice can be auditioned (play/stop) without saving —
 * clicking a name is what saves. The sample language is switchable DE/EN/ES,
 * seeded from the UI language (mirrors the TTS VoicePicker).
 */
function RealtimeVoiceRow({
  providerId,
  label,
  value,
  model,
  options,
  saving,
  onChange,
}: {
  providerId: string;
  label: string;
  value: string;
  model: string;
  options: RealtimeOptionInfo[];
  saving: boolean;
  onChange: (value: string) => void;
}) {
  const t = useT();
  const uiLang = useUiLanguage();
  const pushToast = useEventStore((s) => s.pushToast);

  const [open, setOpen] = useState(false);
  const [previewLang, setPreviewLang] = useState<"de" | "en" | "es">(
    uiLang === "de" ? "de" : uiLang === "es" ? "es" : "en",
  );
  // The voice currently PLAYING vs. the voice whose audio is being FETCHED —
  // the preview button shows a spinner while loading, a stop icon while
  // playing (same contract as the TTS VoicePicker).
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  function stopPreview() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setPreviewingId(null);
    setLoadingId(null);
  }

  // Stop and free any playing preview when the component unmounts.
  useEffect(() => stopPreview, []);

  // Close the panel on an outside click.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  async function preview(voiceId: string) {
    // Toggle: a second click on the currently-playing / loading voice stops it.
    if (previewingId === voiceId || loadingId === voiceId) {
      stopPreview();
      return;
    }
    stopPreview();
    setLoadingId(voiceId);
    try {
      const blob = await fetchRealtimeVoicePreview({
        providerId,
        voice: voiceId,
        language: previewLang,
        model,
      });
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => stopPreview();
      audio.onerror = () => stopPreview();
      setLoadingId(null);
      setPreviewingId(voiceId);
      await audio.play();
    } catch (e) {
      pushToast(
        "error",
        `${t("apikeys_voice.preview_failed")}: ${(e as Error).message}`,
      );
      stopPreview();
    }
  }

  function pick(next: string) {
    setOpen(false);
    if (next !== value) onChange(next);
  }

  const currentEntry = options.find((o) => o.id === value);

  return (
    <div ref={rootRef} className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <button
          type="button"
          aria-label={label}
          aria-expanded={open}
          disabled={saving}
          onClick={() => setOpen((o) => !o)}
          className={cn(
            "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md border bg-background px-2 py-1 text-left text-xs transition-colors disabled:opacity-60",
            open
              ? "border-primary/50 ring-1 ring-primary/20"
              : "border-input hover:border-primary/40",
          )}
        >
          <span className={cn("truncate", !value && "text-muted-foreground")}>
            {value
              ? currentEntry?.label || value
              : t("apikeys_view.realtime_provider_default")}
          </span>
          {saving ? (
            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <ChevronDown
              className={cn(
                "h-3 w-3 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          )}
        </button>
        {/* The provider-default pick ("") resolves server-side — there is no
            single voice to honestly sample, so the trigger-row preview only
            renders for a concrete voice. */}
        {value && (
          <PreviewButton
            active={previewingId === value}
            loading={loadingId === value}
            onClick={() => void preview(value)}
            label={t("apikeys_voice.preview")}
          />
        )}
      </div>

      {/* Inline-expanding voice list with per-voice audition. */}
      {open && (
        <div className="overflow-hidden rounded-md border border-border bg-popover">
          <div
            className="flex items-center justify-end gap-1 border-b border-border px-2.5 py-1"
            role="group"
            aria-label={t("apikeys_voice.preview_language")}
          >
            <span className="text-[10px] text-muted-foreground">
              {t("apikeys_voice.preview_in")}
            </span>
            {(["de", "en", "es"] as const).map((lng) => (
              <button
                key={lng}
                type="button"
                onClick={() => setPreviewLang(lng)}
                aria-pressed={previewLang === lng}
                className={cn(
                  "rounded-full border px-1.5 py-0.5 text-[10px] uppercase tracking-wide transition-colors",
                  previewLang === lng
                    ? "border-primary/40 bg-primary/20 text-primary"
                    : "border-border bg-muted text-muted-foreground hover:text-foreground",
                )}
              >
                {lng}
              </button>
            ))}
          </div>
          <ul className="max-h-56 overflow-y-auto p-1 scrollbar-jarvis">
            <li>
              <div
                className={cn(
                  "flex items-center rounded hover:bg-primary/10",
                  !value && "bg-primary/20",
                )}
              >
                <button
                  type="button"
                  onClick={() => pick(PROVIDER_DEFAULT)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left"
                >
                  <span
                    className={cn(
                      "truncate text-xs",
                      !value && "font-medium text-primary",
                    )}
                  >
                    {t("apikeys_view.realtime_provider_default")}
                  </span>
                  {!value && <Check className="h-3 w-3 shrink-0 text-primary" />}
                </button>
              </div>
            </li>
            {options.map((v) => {
              const isPinned = v.id === value;
              return (
                <li key={v.id}>
                  <div
                    className={cn(
                      "flex items-center rounded hover:bg-primary/10",
                      isPinned && "bg-primary/20",
                    )}
                  >
                    <PreviewButton
                      active={previewingId === v.id}
                      loading={loadingId === v.id}
                      onClick={() => void preview(v.id)}
                      label={t("apikeys_voice.preview")}
                      className="ml-1"
                    />
                    <button
                      type="button"
                      onClick={() => pick(v.id)}
                      className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left"
                    >
                      <span
                        className={cn(
                          "truncate text-xs",
                          isPinned && "font-medium text-primary",
                        )}
                      >
                        {v.label}
                      </span>
                      {isPinned && <Check className="h-3 w-3 shrink-0 text-primary" />}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          {options.length > 0 && (
            <div className="border-t border-border px-2.5 py-1 text-[10px] text-muted-foreground">
              {t("apikeys_voice.count_hint").replace("{0}", String(options.length))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
