import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  getRealtimeOptions,
  saveRealtimeOptions,
  type RealtimeOptionInfo,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

// "" always means "use the provider's own default" — mirrors the backend
// contract (RealtimeOptionsResponse.current_model/current_voice empty when
// nothing is pinned, RealtimeOptionsBody accepting "" to explicitly clear).
const PROVIDER_DEFAULT = "";

/**
 * Per-realtime-provider MODEL + VOICE picker (two compact dropdowns).
 *
 * Unlike every other tier, a realtime session needs BOTH a model and a voice
 * pinned per provider, so this is a small dedicated control against the
 * dedicated `GET/PUT /api/providers/{id}/realtime-options` endpoint rather
 * than the shared search-heavy `BrainModelSelector` — the curated lists are
 * short (a handful of models/voices), so a plain `<select>` is enough.
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
      <RealtimeSelectRow
        label={t("apikeys_view.realtime_voice_label")}
        value={voice}
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
