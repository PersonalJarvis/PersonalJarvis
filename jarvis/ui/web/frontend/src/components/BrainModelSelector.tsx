import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, Loader2, RefreshCw, Search, XCircle } from "lucide-react";
import {
  getBrainProviderModels,
  saveBrainProviderModel,
  type BrainModel,
  type BrainModelProbe,
  type BrainModelSaveResult,
  type ProviderTestStatus,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

const MAX_VISIBLE = 80;

// Mirrors TEST_STATUS_TONE in ApiKeysView (kept local to avoid a circular import).
const STATUS_TONE: Record<ProviderTestStatus, string> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
  not_configured: "border-border bg-muted text-muted-foreground",
  bad_key: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  no_credits: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  rate_limited: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  model_unavailable: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  unreachable: "border-destructive/30 bg-destructive/10 text-destructive",
  error: "border-destructive/30 bg-destructive/10 text-destructive",
};

/**
 * Searchable per-provider model picker — a click-to-open dropdown that expands
 * INLINE (in normal flow) rather than floating absolutely.
 *
 * Inline-expand is deliberate: an absolutely-positioned panel rendered *behind*
 * the (semi-transparent) next provider card — a z-stacking artifact that looked
 * "mushy" with list entries bleeding through. Expanding inline pushes the cards
 * below down instead, so the panel can never be covered or clipped, and it reads
 * as a clean, seamless section of the card.
 *
 * The list is the provider's OWN live catalog (so a freshly released model shows
 * up with no code change); when that catalog is unreachable — e.g. Claude on the
 * Max subscription with no API key — it falls back to the curated current family
 * (Fable / Opus / Sonnet / Haiku). Pick from the list, or type a brand-new id and
 * choose "use custom". The choice is pinned + live-applied and verified with a
 * real 1-token probe whose honest status shows as a chip.
 */
export function BrainModelSelector({
  providerId,
  currentModel,
  recommendedModel,
  onSave,
}: {
  providerId: string;
  currentModel?: string;
  /**
   * The maintainer-recommended model id for this provider (e.g.
   * "gemini-3.5-flash"). When the list contains it, that row gets an
   * "empfohlen" tag. Presentation only — it never changes the pinned value.
   */
  recommendedModel?: string | null;
  /**
   * Override the save target. Used by the Subagent card, which persists to its
   * own endpoint (POST /api/subagent/model) instead of the per-provider model
   * route. Defaults to ``saveBrainProviderModel(providerId, model)``.
   */
  onSave?: (model: string) => Promise<BrainModelSaveResult>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [models, setModels] = useState<BrainModel[]>([]);
  const [source, setSource] = useState<"live" | "cache" | "static" | "curated" | null>(null);
  const [selects, setSelects] = useState<"model" | "voice">("model");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pinned, setPinned] = useState<string>(currentModel ?? "");
  const [probe, setProbe] = useState<BrainModelProbe | null>(null);

  async function load(refresh = false) {
    setLoading(true);
    try {
      const res = await getBrainProviderModels(providerId, refresh);
      setModels(Array.isArray(res.models) ? res.models : []);
      setSource(res.source);
      setSelects(res.selects === "voice" ? "voice" : "model");
      if (!pinned && res.current_model) setPinned(res.current_model);
    } catch (e) {
      pushToast("error", `${t("apikeys_model.load_failed")}: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId]);

  // Close on an outside click (inline panel needs no fixed backdrop).
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

  const matched = useMemo(() => {
    const list = Array.isArray(models) ? models : [];
    const q = query.trim().toLowerCase();
    return q
      ? list.filter(
          (m) => m.id.toLowerCase().includes(q) || m.label.toLowerCase().includes(q),
        )
      : list;
  }, [models, query]);

  const trimmed = query.trim();
  const exactMatch = matched.some((m) => m.id === trimmed);
  const visible = matched.slice(0, MAX_VISIBLE);
  const pinnedLabel = models.find((m) => m.id === pinned)?.label ?? pinned;

  async function save(value: string) {
    const model = value.trim();
    setSaving(true);
    setProbe(null);
    setOpen(false);
    setQuery("");
    try {
      const res = onSave ? await onSave(model) : await saveBrainProviderModel(providerId, model);
      setPinned(res.model);
      setProbe(res.probe ?? null);
      const note = res.restart_required
        ? ` ${t("apikeys_model.restart_note")}`
        : res.applied_live
          ? ` ${t("apikeys_model.live_note")}`
          : "";
      pushToast("success", `${t("apikeys_model.saved")}${note}`);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      ref={rootRef}
      className="space-y-1.5"
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {selects === "voice" ? t("apikeys_model.heading_voice") : t("apikeys_model.heading")}
        </span>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          title={t("apikeys_model.refresh")}
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          {t("apikeys_model.refresh")}
        </button>
      </div>

      {/* Trigger — reads like a <select> */}
      <button
        type="button"
        aria-label={t("apikeys_model.model_label")}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        disabled={saving}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-md border bg-background px-3 py-2 text-left transition-colors",
          open ? "border-primary/50 ring-1 ring-primary/20" : "border-input hover:border-primary/40",
        )}
      >
        <span className={cn("truncate text-xs", !pinned && "text-muted-foreground")}>
          {pinnedLabel ||
            (selects === "voice"
              ? t("apikeys_model.choose_voice")
              : t("apikeys_model.choose_placeholder"))}
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

      {/* Inline-expanding panel — in normal flow, so it pushes the cards below
          down instead of floating over (and behind) them. */}
      {open && (
        <div className="overflow-hidden rounded-md border border-border bg-popover">
          <div className="flex items-center gap-2 border-b border-border px-2.5 py-1.5">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <input
              type="text"
              autoFocus
              aria-label={t("apikeys_model.search_placeholder")}
              value={query}
              placeholder={t("apikeys_model.search_placeholder")}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full bg-transparent font-mono text-xs focus:outline-none"
            />
          </div>

          <ul className="max-h-56 overflow-y-auto p-1 scrollbar-jarvis">
            {visible.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => void save(m.id)}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-accent",
                    m.id === pinned && "bg-primary/10",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-1.5 truncate text-xs">
                    {m.label}
                    {recommendedModel && m.id === recommendedModel && (
                      <span className="shrink-0 rounded-full bg-primary/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-primary">
                        {t("apikeys_model.recommended_tag")}
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {m.id}
                  </span>
                </button>
              </li>
            ))}

            {trimmed && !exactMatch && (
              <li>
                <button
                  type="button"
                  data-testid="use-custom-row"
                  onClick={() => void save(trimmed)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent"
                >
                  <Check className="h-3 w-3 shrink-0 text-primary" />
                  {t("apikeys_model.use_custom").replace("{0}", trimmed)}
                </button>
              </li>
            )}

            {!visible.length && !trimmed && (
              <li className="px-2 py-2 text-[11px] text-muted-foreground">
                {loading ? t("apikeys_model.loading") : t("apikeys_model.no_models")}
              </li>
            )}
          </ul>

          {matched.length > MAX_VISIBLE && (
            <div className="border-t border-border px-2.5 py-1 text-[10px] text-muted-foreground">
              {t("apikeys_model.more_hint").replace("{0}", String(matched.length - MAX_VISIBLE))}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {source === "static" && (
          <span className="text-[11px] text-amber-600" title={t("apikeys_model.source_static_note")}>
            {t("apikeys_model.source_static")}
          </span>
        )}
        {probe && <ProbeChip probe={probe} />}
      </div>
    </div>
  );
}

function ProbeChip({ probe }: { probe: BrainModelProbe }) {
  const t = useT();
  const tone = STATUS_TONE[probe.status];
  return (
    <span
      data-testid="brain-model-probe"
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
        tone,
      )}
      title={[probe.detail, probe.latency_ms ? `${Math.round(probe.latency_ms)} ms` : ""]
        .filter(Boolean)
        .join("\n")}
    >
      {probe.status === "ok" ? (
        <Check className="h-3 w-3" />
      ) : probe.integration_ok ? (
        <AlertCircle className="h-3 w-3" />
      ) : (
        <XCircle className="h-3 w-3" />
      )}
      {t(`apikeys_test.status_${probe.status}`)}
    </span>
  );
}
