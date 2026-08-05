import { useEffect, useId, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, Loader2 } from "lucide-react";
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
 * Realtime providers expose a model and a voice configuration, so this is a
 * small dedicated control against the
 * dedicated `GET/PUT /api/providers/{id}/realtime-options` endpoint rather
 * than the shared search-heavy `BrainModelSelector`.
 *
 * Both choices use app-styled pickers so Windows does not replace them with
 * a low-contrast native menu. A provider-managed model is shown as read-only
 * instead of pretending the user can pick a model that the server rejects.
 * Providers with a standalone sampler
 * expose per-voice audio previews; offer-only transports remain selectable
 * without showing a preview button that cannot work.
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
  const [previewAvailable, setPreviewAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [savingField, setSavingField] = useState<"model" | "voice" | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError(false);
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
        setPreviewAvailable(r?.preview_available === true);
      })
      .catch(() => {
        if (alive) setLoadError(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [providerId, reloadNonce]);

  // The pin can move without this card: a voice command, the control CLI, or a
  // second window all write `[brain.providers.<id>].model/voice` and announce
  // it. Without this the card kept showing the previous voice — a picker that
  // quietly disagrees with what the next call will actually use.
  useEffect(() => {
    const modelKey = `brain.providers.${providerId}.model`;
    const voiceKey = `brain.providers.${providerId}.voice`;
    function onConfigReloaded(event: Event) {
      const detail = (event as CustomEvent<{ changed_keys?: unknown }>).detail;
      const keys = Array.isArray(detail?.changed_keys)
        ? (detail.changed_keys as string[])
        : [];
      if (keys.includes(modelKey) || keys.includes(voiceKey)) {
        setReloadNonce((value) => value + 1);
      }
    }
    function onRealtimeSwitched() {
      setReloadNonce((value) => value + 1);
    }
    window.addEventListener("jarvis:config-reloaded", onConfigReloaded);
    window.addEventListener("jarvis:realtime-switched", onRealtimeSwitched);
    return () => {
      window.removeEventListener("jarvis:config-reloaded", onConfigReloaded);
      window.removeEventListener("jarvis:realtime-switched", onRealtimeSwitched);
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

  if (loading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 py-1 text-xs text-muted-foreground"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        {t("apikeys_view.realtime_options_loading")}
      </div>
    );
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-2.5 py-2 text-xs"
      >
        <span className="flex min-w-0 items-center gap-2 text-destructive">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t("apikeys_view.realtime_options_error")}
        </span>
        <button
          type="button"
          onClick={() => setReloadNonce((value) => value + 1)}
          className="shrink-0 rounded-md border border-destructive/30 px-2 py-1 font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          {t("apikeys_view.realtime_options_retry")}
        </button>
      </div>
    );
  }

  return (
    <div
      className="space-y-1.5"
      // Configuration clicks must not bubble to the card's activate handler.
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <RealtimeModelRow
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
        previewAvailable={previewAvailable}
        onChange={(next) => void handleChange("voice", next)}
      />
    </div>
  );
}

function RealtimeModelRow({
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
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const managed = options.length === 1 && options[0].id === "auto";
  const currentEntry = options.find((option) => option.id === value);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (managed) {
    return (
      <div className="flex items-start gap-2">
        <span className="w-14 shrink-0 pt-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <div
          aria-label={label}
          className="min-w-0 flex-1 rounded-md border border-primary/20 bg-primary/[0.06] px-2.5 py-1.5"
        >
          <div className="flex items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate font-medium">
              {options[0].label}
            </span>
            <span className="rounded-full border border-primary/25 bg-primary/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary">
              {t("apikeys_view.realtime_model_managed")}
            </span>
          </div>
          <p className="mt-0.5 text-[10px] leading-snug text-muted-foreground">
            {t("apikeys_view.realtime_model_managed_hint")}
          </p>
        </div>
      </div>
    );
  }

  function pick(next: string) {
    setOpen(false);
    if (next !== value) onChange(next);
  }

  return (
    <div ref={rootRef} className="relative flex items-center gap-2">
      <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        disabled={saving}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md border bg-background px-2.5 py-1.5 text-left text-xs disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
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
          <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />
        ) : (
          <ChevronDown
            className={cn(
              "h-3 w-3 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
            aria-hidden="true"
          />
        )}
      </button>
      {open && (
        <ul
          id={panelId}
          className="absolute left-16 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-xl scrollbar-jarvis"
        >
          <ModelOption
            label={t("apikeys_view.realtime_provider_default")}
            selected={!value}
            onClick={() => pick(PROVIDER_DEFAULT)}
          />
          {options.map((option) => (
            <ModelOption
              key={option.id}
              label={option.label}
              selected={option.id === value}
              onClick={() => pick(option.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ModelOption({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary",
          selected && "bg-primary/20 font-medium text-primary",
        )}
      >
        <span className="truncate">{label}</span>
        {selected && <Check className="h-3 w-3 shrink-0" aria-hidden="true" />}
      </button>
    </li>
  );
}

/**
 * Voice row: current pick, expanding into the full selectable voice list.
 * When the backend advertises a sampler, every voice can be auditioned without
 * saving and the sample language follows the same DE/EN/ES control as TTS.
 */
function RealtimeVoiceRow({
  providerId,
  label,
  value,
  model,
  options,
  saving,
  previewAvailable,
  onChange,
}: {
  providerId: string;
  label: string;
  value: string;
  model: string;
  options: RealtimeOptionInfo[];
  saving: boolean;
  previewAvailable: boolean;
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
  const panelId = useId();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const previewGenerationRef = useRef(0);

  function stopPreview() {
    previewGenerationRef.current += 1;
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

  // Close the panel on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function preview(voiceId: string) {
    // Toggle: a second click on the currently-playing / loading voice stops it.
    if (previewingId === voiceId || loadingId === voiceId) {
      stopPreview();
      return;
    }
    stopPreview();
    const generation = previewGenerationRef.current;
    setLoadingId(voiceId);
    try {
      const blob = await fetchRealtimeVoicePreview({
        providerId,
        voice: voiceId,
        language: previewLang,
        model,
      });
      if (previewGenerationRef.current !== generation) return;
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        if (previewGenerationRef.current === generation) stopPreview();
      };
      audio.onerror = () => {
        if (previewGenerationRef.current === generation) stopPreview();
      };
      setLoadingId(null);
      setPreviewingId(voiceId);
      await audio.play();
    } catch (e) {
      if (previewGenerationRef.current !== generation) return;
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
          aria-controls={panelId}
          disabled={saving}
          onClick={() => setOpen((o) => !o)}
          className={cn(
            "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md border bg-background px-2 py-1 text-left text-xs transition-colors disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
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
        {previewAvailable && value && (
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
        <div
          id={panelId}
          className="overflow-hidden rounded-md border border-border bg-popover"
        >
          {previewAvailable && (
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
          )}
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
                    {previewAvailable && (
                      <PreviewButton
                        active={previewingId === v.id}
                        loading={loadingId === v.id}
                        onClick={() => void preview(v.id)}
                        label={t("apikeys_voice.preview")}
                        className="ml-1"
                      />
                    )}
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
