import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Loader2, Play, Square } from "lucide-react";
import {
  fetchTtsPreview,
  getBrainProviderModels,
  getTtsVoices,
  saveBrainProviderModel,
  saveTtsVoice,
  type BrainModelSaveResult,
  type TtsVoiceEntry,
} from "@/hooks/useProviders";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT, useUiLanguage } from "@/i18n";

/**
 * The OpenRouter-TTS provider card's model + voice controls.
 *
 * OpenRouter TTS is the one TTS provider where the user picks BOTH a model and,
 * within that model, a specific VOICE. This wraps the shared model picker and,
 * once a model is chosen, renders a language-tagged voice picker with an audio
 * preview below it. Both the model and the voice write to the global ``[tts]``
 * block (model → ``[tts] model``, voice → ``[tts] voice_de``/``voice_en``).
 */
export function OpenRouterTtsControls({
  providerId,
  recommendedModel,
  healthActive = false,
}: {
  providerId: string;
  recommendedModel?: string | null;
  healthActive?: boolean;
}) {
  // The currently selected TTS model — seeded from the catalog's current
  // selection and updated when the model picker below saves a new one, so the
  // voice picker always lists voices for the RIGHT model.
  const [model, setModel] = useState<string>("");

  useEffect(() => {
    let alive = true;
    void getBrainProviderModels(providerId)
      .then((r) => {
        if (alive) setModel(r.current_model || "");
      })
      .catch(() => {
        /* the model picker below surfaces its own load error */
      });
    return () => {
      alive = false;
    };
  }, [providerId]);

  async function handleModelSave(m: string): Promise<BrainModelSaveResult> {
    const res = await saveBrainProviderModel(providerId, m);
    // Reflect the newly chosen model so the voice list re-fetches for it.
    setModel(res.model);
    return res;
  }

  return (
    <div className="space-y-3">
      <BrainModelSelector
        providerId={providerId}
        recommendedModel={recommendedModel}
        onSave={handleModelSave}
        healthSection="tts"
        healthActive={healthActive}
      />
      {model && (
        <VoicePicker
          model={model}
          providerId={providerId}
          healthActive={healthActive}
        />
      )}
    </div>
  );
}

/** Flag + short label for a language code; "multi" → 🌐, unknown → the code. */
function languageChip(
  t: (k: string) => string,
  code: string,
): { flag: string; label: string } {
  switch (code) {
    case "de":
      return { flag: "🇩🇪", label: "DE" };
    case "en":
      return { flag: "🇬🇧", label: "EN" };
    case "es":
      return { flag: "🇪🇸", label: "ES" };
    case "fr":
      return { flag: "🇫🇷", label: "FR" };
    case "multi":
      return { flag: "🌐", label: t("apikeys_voice.multilingual") };
    default:
      return { flag: "", label: code.toUpperCase() };
  }
}

function LanguageChip({ code }: { code: string }) {
  const t = useT();
  const { flag, label } = languageChip(t, code);
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-muted px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">
      {flag && <span aria-hidden>{flag}</span>}
      {label}
    </span>
  );
}

/**
 * Language-tagged voice picker with a per-voice audio preview. Lists the chosen
 * model's voices (each with a language chip), persists a pick, and plays a short
 * spoken sample on demand — the sample language is switchable DE/EN.
 */
function VoicePicker({
  model,
  providerId,
  healthActive,
}: {
  model: string;
  providerId: string;
  healthActive: boolean;
}) {
  const t = useT();
  const uiLang = useUiLanguage();
  const pushToast = useEventStore((s) => s.pushToast);

  const [voices, setVoices] = useState<TtsVoiceEntry[]>([]);
  const [pinned, setPinned] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);
  // The sample language — defaults to the app's UI language (DE/EN/ES are the
  // supported sample languages; any other locale falls back to EN).
  const [previewLang, setPreviewLang] = useState<"de" | "en" | "es">(
    uiLang === "de" ? "de" : uiLang === "es" ? "es" : "en",
  );
  // The voice currently PLAYING vs. the voice whose audio is being FETCHED — the
  // preview button shows a spinner while loading, a stop icon while playing.
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

  // Load the model's voices; seed the pinned voice from the persisted value (or
  // the model default). Re-runs whenever the selected model changes.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    stopPreview();
    void getTtsVoices(model)
      .then((r) => {
        if (!alive) return;
        setVoices(r.voices);
        setPinned(r.current || r.default || "");
      })
      .catch((e) => {
        if (alive)
          pushToast(
            "error",
            `${t("apikeys_voice.load_failed")}: ${(e as Error).message}`,
          );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

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
      const blob = await fetchTtsPreview({ model, voice: voiceId, language: previewLang });
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

  async function save(voiceId: string) {
    setSaving(true);
    setOpen(false);
    if (healthActive) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-selection-pending", {
          detail: { section: "tts", provider: providerId },
        }),
      );
    }
    try {
      const res = await saveTtsVoice(voiceId);
      setPinned(voiceId);
      if (healthActive) {
        window.dispatchEvent(
          new CustomEvent("jarvis:provider-config-changed", {
            detail: { section: "tts", provider: providerId },
          }),
        );
      }
      const note = res.restart_required
        ? ` ${t("apikeys_model.restart_note")}`
        : res.applied_live
          ? ` ${t("apikeys_model.live_note")}`
          : "";
      pushToast("success", `${t("apikeys_voice.saved")}${note}`);
    } catch (e) {
      if (healthActive) {
        window.dispatchEvent(
          new CustomEvent("jarvis:provider-switch-failed", {
            detail: { section: "tts", provider: providerId },
          }),
        );
      }
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const pinnedEntry = voices.find((v) => v.id === pinned);

  return (
    <div
      ref={rootRef}
      className="space-y-1.5"
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {t("apikeys_voice.heading")}
        </span>
        {/* Preview-language toggle (the sample sentence's language). */}
        <div
          className="inline-flex items-center gap-1"
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
      </div>

      {/* Trigger: current voice + a play button for it. */}
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-label={t("apikeys_voice.voice_label")}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          disabled={saving}
          className={cn(
            "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md border bg-background px-3 py-2 text-left transition-colors",
            open
              ? "border-primary/50 ring-1 ring-primary/20"
              : "border-input hover:border-primary/40",
          )}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            {pinnedEntry && <LanguageChip code={pinnedEntry.language} />}
            <span className={cn("truncate text-xs", !pinned && "text-muted-foreground")}>
              {pinned || t("apikeys_voice.choose_voice")}
            </span>
          </span>
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          )}
        </button>
        {pinned && (
          <PreviewButton
            active={previewingId === pinned}
            loading={loadingId === pinned}
            onClick={() => void preview(pinned)}
            label={t("apikeys_voice.preview")}
          />
        )}
      </div>

      {/* Inline-expanding voice list. */}
      {open && (
        <div className="overflow-hidden rounded-md border border-border bg-popover">
          <ul className="max-h-56 overflow-y-auto p-1 scrollbar-jarvis">
            {voices.map((v) => {
              const isPinned = v.id === pinned;
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
                      onClick={() => void save(v.id)}
                      className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left"
                    >
                      <span
                        className={cn(
                          "flex min-w-0 items-center gap-1.5 truncate text-xs",
                          isPinned && "font-medium text-primary",
                        )}
                      >
                        <LanguageChip code={v.language} />
                        <span className="truncate font-mono">{v.id}</span>
                      </span>
                      {isPinned && <Check className="h-3 w-3 shrink-0 text-primary" />}
                    </button>
                  </div>
                </li>
              );
            })}

            {!voices.length && (
              <li className="px-2 py-2 text-[11px] text-muted-foreground">
                {loading ? t("apikeys_model.loading") : t("apikeys_voice.no_voices")}
              </li>
            )}
          </ul>
          {voices.length > 0 && (
            <div className="border-t border-border px-2.5 py-1 text-[10px] text-muted-foreground">
              {t("apikeys_voice.count_hint").replace("{0}", String(voices.length))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** A small play / stop button with a loading spinner while the audio fetches.
 * Shared with the realtime voice picker (RealtimeOptionsControl). */
export function PreviewButton({
  active,
  loading,
  onClick,
  label,
  className,
}: {
  active: boolean;
  loading?: boolean;
  onClick: () => void;
  label: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-busy={loading || undefined}
      className={cn(
        "shrink-0 rounded p-1 text-muted-foreground transition-colors hover:text-primary",
        (active || loading) && "text-primary",
        className,
      )}
    >
      {loading ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : active ? (
        <Square className="h-3 w-3 fill-current" />
      ) : (
        <Play className="h-3 w-3" />
      )}
    </button>
  );
}
