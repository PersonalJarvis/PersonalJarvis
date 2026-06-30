import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ExternalLink,
  Loader2,
  RefreshCw,
  Search,
  Star,
  XCircle,
} from "lucide-react";
import {
  getBrainProviderModels,
  saveBrainProviderModel,
  type BrainModel,
  type BrainModelProbe,
  type BrainModelSaveResult,
  type ProviderTestStatus,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The provider's own page for a model, so the user can see its details/pricing.
 * Only OpenRouter exposes a stable per-model URL whose path IS the model id
 * (`openrouter.ai/anthropic/claude-opus-4.8`). Direct providers (claude-api /
 * openai / gemini) have no clean per-model page → no link (better than a dead
 * or guessed one). Returns null when there is no reliable URL.
 */
function modelProviderUrl(providerId: string, modelId: string): string | null {
  if (providerId === "openrouter" && modelId) {
    return `https://openrouter.ai/${modelId}`;
  }
  return null;
}


/**
 * Catalog filter chips — narrow the (already search-filtered) list to one
 * quality band. ``starred`` = maintainer favourites, ``free`` = zero-cost,
 * ``frontier`` = flagship band, ``value`` = strong price/performance band. The
 * tags are computed once in the backend (classify_model) so the chips can never
 * drift from the list order. Presentation only — a chip never changes what is
 * pinned. The list of keys IS the render order of the chip row.
 */
type ModelFilter = "all" | "starred" | "free" | "frontier" | "value";

const MODEL_FILTERS: readonly ModelFilter[] = [
  "all",
  "starred",
  "free",
  "frontier",
  "value",
] as const;

function matchesFilter(m: BrainModel, f: ModelFilter): boolean {
  switch (f) {
    case "starred":
      return !!m.starred;
    case "free":
      return !!m.free;
    case "frontier":
      return !!m.frontier;
    case "value":
      return !!m.value;
    default:
      return true;
  }
}

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
  headingLabel,
  placeholder,
  controlled,
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
  /**
   * Override the section heading (e.g. "Computer-Use model"). Defaults to the
   * standard model/voice heading. Presentation only.
   */
  headingLabel?: string;
  /** Override the empty-selection placeholder text on the trigger. */
  placeholder?: string;
  /**
   * Controlled mode: ``pinned`` follows the ``currentModel`` prop (synced on
   * change) and the live catalog's ``current_model`` NEVER overrides it. Used by
   * the Computer-Use model picker, whose pinned value is a SEPARATE selection
   * (cu_model) the parent owns — not the provider's main model that
   * ``GET /models`` reports. Off by default, so the standard uncontrolled
   * behaviour (auto-pin from the catalog) is unchanged.
   */
  controlled?: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ModelFilter>("all");
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
      // Uncontrolled: adopt the catalog's current selection as the pinned value.
      // Controlled (CU picker): the parent owns the pinned value (cu_model) —
      // never overwrite it with the provider's main model from GET /models.
      if (!controlled && !pinned && res.current_model) setPinned(res.current_model);
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

  // Controlled mode: keep the displayed pinned value in sync with the parent's
  // currentModel (e.g. the CU picker resolves cu_model asynchronously after
  // mount). No-op in the default uncontrolled mode.
  useEffect(() => {
    if (controlled) setPinned(currentModel ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controlled, currentModel]);

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
    if (!q) return list;
    // Separator-insensitive match: model ids use hyphens/slashes/dots
    // (openai/gpt-5.5) but users type spaces ("gpt 5.5") or nothing
    // ("gpt5.5"). Strip every non-alphanumeric char from both sides so the
    // query matches regardless of separators, while still honouring a plain
    // substring hit (e.g. searching a literal "/").
    const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const sq = squash(query);
    return list.filter((m) => {
      const id = m.id.toLowerCase();
      const label = m.label.toLowerCase();
      return (
        id.includes(q) ||
        label.includes(q) ||
        (sq.length > 0 && (squash(m.id).includes(sq) || squash(m.label).includes(sq)))
      );
    });
  }, [models, query]);

  const trimmed = query.trim();
  const exactMatch = matched.some((m) => m.id === trimmed);
  // Per-filter hit counts over the SEARCH-filtered list, so each chip shows how
  // many of the current results it would keep and an empty band can disable its
  // chip instead of dead-ending the user on a blank list.
  const filterCounts = useMemo(() => {
    const counts: Record<ModelFilter, number> = {
      all: matched.length,
      starred: 0,
      free: 0,
      frontier: 0,
      value: 0,
    };
    for (const m of matched) {
      if (m.starred) counts.starred += 1;
      if (m.free) counts.free += 1;
      if (m.frontier) counts.frontier += 1;
      if (m.value) counts.value += 1;
    }
    return counts;
  }, [matched]);
  // Only brain-model catalogs carry tags; the chip row stays hidden for the
  // voice/STT pickers (and any tag-less list) so it never adds dead controls.
  const hasTags = useMemo(
    () => models.some((m) => m.starred || m.free || m.frontier || m.value),
    [models],
  );
  // Show the COMPLETE catalog — every model is visible on open and every search
  // hit is shown. The list lives in a scrollable container, so even OpenRouter's
  // full ~340-model catalog is fully reachable without a display cap. The active
  // band chip narrows it further (presentation only — never gates the pin).
  const visible = useMemo(
    () => matched.filter((m) => matchesFilter(m, filter)),
    [matched, filter],
  );
  const pinnedLabel = models.find((m) => m.id === pinned)?.label ?? pinned;

  // If a search narrows the active band to zero hits, drop back to "all" so the
  // user never stares at an empty list behind a still-active band chip.
  useEffect(() => {
    if (filter !== "all" && filterCounts[filter] === 0) setFilter("all");
  }, [filter, filterCounts]);

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
          {headingLabel ??
            (selects === "voice" ? t("apikeys_model.heading_voice") : t("apikeys_model.heading"))}
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
            placeholder ||
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

          {/* Quality-band filter chips — narrow the list to free / frontier /
              best-value / starred. Only shown for catalogs that carry tags (the
              brain-model pickers), never for the voice/STT lists. */}
          {hasTags && (
            <div className="flex flex-wrap items-center gap-1 border-b border-border px-2 py-1.5">
              {MODEL_FILTERS.map((f) => {
                const count = filterCounts[f];
                const isActive = filter === f;
                // A band with no current hit is shown but disabled, so the chip
                // row stays stable while typing instead of reflowing.
                const disabled = f !== "all" && count === 0;
                return (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setFilter(f)}
                    disabled={disabled}
                    aria-pressed={isActive}
                    aria-label={t(`apikeys_model.filter_${f}`)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors",
                      isActive
                        ? "border-primary/40 bg-primary/20 text-primary"
                        : "border-border bg-muted text-muted-foreground hover:text-foreground",
                      disabled && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
                    )}
                  >
                    {f === "starred" && <Star className="h-2.5 w-2.5 fill-current" />}
                    {t(`apikeys_model.filter_${f}`)}
                    {f !== "all" && <span className="tabular-nums opacity-60">{count}</span>}
                  </button>
                );
              })}
            </div>
          )}

          <ul className="max-h-56 overflow-y-auto p-1 scrollbar-jarvis">
            {visible.map((m) => {
              const link = modelProviderUrl(providerId, m.id);
              const isPinned = m.id === pinned;
              return (
                <li key={m.id}>
                  {/* Row is a <div> (not a button) so the external-link button can
                      sit beside the select button without nesting <button>s. The
                      hover/selected highlight lives on the row. Hover uses a soft
                      gold TINT (not the loud full-gold accent) so light text stays
                      readable — the maintainer's "too bright" complaint. */}
                  <div
                    className={cn(
                      "flex items-center rounded hover:bg-primary/10",
                      isPinned && "bg-primary/20",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => void save(m.id)}
                      className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left"
                    >
                      <span
                        className={cn(
                          "flex min-w-0 items-center gap-1.5 truncate text-xs",
                          isPinned && "font-medium text-primary",
                        )}
                      >
                        {m.starred && (
                          <Star
                            aria-label={t("apikeys_model.starred_label")}
                            className="h-3 w-3 shrink-0 fill-primary text-primary"
                          />
                        )}
                        {m.label}
                        {recommendedModel && m.id === recommendedModel && (
                          <span className="shrink-0 rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary-foreground">
                            {t("apikeys_model.recommended_tag")}
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                        {m.id}
                      </span>
                    </button>
                    {link && (
                      <button
                        type="button"
                        onClick={() => void openExternalUrl(link)}
                        aria-label={t("apikeys_model.open_on_provider")}
                        title={t("apikeys_model.open_on_provider")}
                        className="mr-1 shrink-0 rounded p-1 text-muted-foreground transition-colors hover:text-primary"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </li>
              );
            })}

            {trimmed && !exactMatch && (
              <li>
                <button
                  type="button"
                  data-testid="use-custom-row"
                  onClick={() => void save(trimmed)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-primary/10"
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

          {matched.length > 0 && (
            <div className="border-t border-border px-2.5 py-1 text-[10px] text-muted-foreground">
              {t("apikeys_model.count_hint").replace("{0}", String(matched.length))}
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
