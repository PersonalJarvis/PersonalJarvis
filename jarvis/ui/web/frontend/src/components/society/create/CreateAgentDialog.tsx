/**
 * Create an agent: the left column is what a person actually decides, the
 * character on the right — live, turnable, from above and below — with the
 * look editor under it (MASTERPLAN §4.2, agent-definition §6: no wizard).
 *
 * Left, top to bottom (maintainer, 2026-09-02, modelled on Grok Bot's "New
 * Bot" sheet): name, title, description; "Runs on" — ONLY the seats that are
 * connected on this machine, subscriptions first (a CLI on a plan bills the
 * plan, not per token), then saved API keys, then local models, with the
 * login to use when a CLI has more than one, the model (a keyed row's live
 * list, Ollama's installed models) and the effort; and a "More" disclosure
 * for the permission ceiling and the daily budget. A provider that is not
 * connected is not listed — a local row counts as connected only when it
 * answers with models — and one sentence says where to connect it. No tool
 * picking here: every agent has everything Jarvis has connected, and what it
 * reaches for first is settled in its own chat afterwards.
 *
 * Everything a person changes is visible in the preview the same frame: a
 * preset, a colour, the build, the height. What is not built yet is shown
 * disabled with the reason (the small/large builds), never hidden.
 *
 * Until the society backend is bound, "Create" appends a sample row to this
 * window's roster (data.ts, the single swap point) and opens its card.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Shuffle, Upload, X } from "lucide-react";

import { effortLabel } from "@/components/agentchat/AgentComposer";
import { AgentMark } from "@/components/agentic/AgentMark";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { Combobox, isComboboxPanelEvent, type ComboboxGroup } from "@/components/ui/combobox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import {
  fetchAgentChatCatalog,
  fetchAgentConnections,
  fetchProviderModels,
  type AgentChatProvider,
  type AgentConnectionRow,
  type CuratedModel,
} from "@/lib/agentChatApi";
import { fetchSocietyProviders, type SocietyProviderRow } from "@/lib/societyApi";
import { cn } from "@/lib/utils";
import { joinProviderOptions } from "@/store/agentChat";

import {
  accountChoice,
  accountHint,
  brainSeats,
  defaultSeat,
  effortsFor,
  modelsFor,
  type BrainKind,
  type BrainSeat,
} from "./brainPicker";
import { useCreateAgent, type PermissionCeiling } from "../data";
import { AgentFigureViewer } from "../figures/AgentFigureViewer";
import {
  EDITABLE_CELLS,
  PALETTE_PRESETS,
  defaultRecipe,
  resolvePalette,
  shufflePalette,
  type FigureRecipe,
  type PaletteCell,
} from "../figures/figureRecipe";
import {
  basesForStyle,
  catalogBaseFor,
  isReservedStyle,
  keepablePartsFor,
  partsForSlot,
  slotsWithParts,
  stylesWithBases,
  CATALOG,
} from "../figures/figureRegistry";
import type { FigureArchetype } from "../figures/figureRecipe";

const CEILINGS: readonly PermissionCeiling[] = ["safe", "monitor", "ask"];

/**
 * How tall a figure may be made, per archetype. One 1.5–2.1 m band fits a
 * person and nothing else: a fox at 1.75 m is a horse, and Gigi's own scale
 * is 1.9 m. The band is the archetype's contract height ±25 %.
 */
const HEIGHT_RANGE: Readonly<Record<FigureArchetype, { min: number; max: number }>> = {
  biped: { min: 1.5, max: 2.1 },
  quadruped: { min: 0.6, max: 1.3 },
  spirit: { min: 1.5, max: 2.4 },
};
const IMPORTED_RANGE = { min: 0.6, max: 2.4 };

/** What the creator opens on; the base comes from the style, never the other way round. */
const DEFAULT_STYLE = "modern";

/** True while the recipe still wears its own base's stock colours, cell for cell. */
function untouchedPalette(recipe: FigureRecipe): boolean {
  const stock = catalogBaseFor(recipe)?.palette;
  if (!stock) return false;
  const worn = recipe.palette ?? {};
  return Object.entries(worn).every(([cell, value]) => stock[cell as PaletteCell] === value);
}

export interface CreateAgentDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (agentId: string) => void;
}

export function CreateAgentDialog({ open, onClose, onCreated }: CreateAgentDialogProps) {
  const t = useT();
  const createAgent = useCreateAgent();
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [recipe, setRecipe] = useState<FigureRecipe>(() => defaultRecipe(DEFAULT_STYLE));
  const [style, setStyle] = useState<string>(DEFAULT_STYLE);
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [accountId, setAccountId] = useState("");
  const [ceiling, setCeiling] = useState<PermissionCeiling>("monitor");
  const [budget, setBudget] = useState("2");
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importProblems, setImportProblems] = useState<string[] | null>(null);
  const [importedName, setImportedName] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The seats: the same catalog the typed chat offers — API families, CLI
  // seats, local models, filtered by the society surface on the backend —
  // joined with the Agents tab's credential truth exactly as the chat's
  // composer joins it, plus the subscription logins stored per CLI.
  const catalog = useQuery({
    queryKey: ["agent-chat", "catalog", "society"],
    queryFn: () => fetchAgentChatCatalog("society"),
    enabled: open,
    staleTime: 60_000,
  });
  const connections = useQuery({
    queryKey: ["agent-chat", "connections"],
    queryFn: () => fetchAgentConnections().catch(() => [] as AgentConnectionRow[]),
    enabled: open,
    staleTime: 60_000,
  });
  const societyProviders = useQuery({
    queryKey: ["society", "providers"],
    queryFn: () => fetchSocietyProviders().catch(() => [] as SocietyProviderRow[]),
    enabled: open,
    staleTime: 60_000,
  });
  // A keyless row (Ollama, a local server) is listed only when it answers
  // with models, so those lists are fetched before the picker fills; a keyed
  // row's live list is fetched once it is picked, like the composer does.
  const keylessIds = useMemo(
    () => (catalog.data?.providers ?? []).filter((p) => p.keyless && p.models_source === "live").map((p) => p.id),
    [catalog.data],
  );
  const keylessModels = useQuery({
    queryKey: ["agent-chat", "live-models", "keyless", keylessIds],
    queryFn: () => fetchModelLists(keylessIds),
    enabled: open && catalog.isSuccess,
    staleTime: 60_000,
  });
  const pickedLive: AgentChatProvider | undefined = (catalog.data?.providers ?? []).find(
    (p) => p.id === providerId && p.models_source === "live" && !p.keyless,
  );
  const pickedModels = useQuery({
    queryKey: ["agent-chat", "live-models", pickedLive?.id ?? ""],
    queryFn: () => fetchModelLists(pickedLive ? [pickedLive.id] : []),
    enabled: open && Boolean(pickedLive),
    staleTime: 60_000,
  });
  const liveModels = useMemo(
    () => ({ ...(keylessModels.data ?? {}), ...(pickedModels.data ?? {}) }),
    [keylessModels.data, pickedModels.data],
  );

  const seatsLoading =
    catalog.isLoading || connections.isLoading || societyProviders.isLoading || keylessModels.isLoading;
  // Joined only once every answer is in (each query settles to [] on a
  // failure): a join over a catalog without the credential rows would call
  // every API seat unconnected, list the local rows alone, and the default
  // pick would land on one of them before the keys arrive.
  const seats = useMemo<BrainSeat[]>(() => {
    const providers = catalog.data?.providers ?? [];
    if (!providers.length || !connections.data || !societyProviders.data || !keylessModels.data) return [];
    return brainSeats(
      joinProviderOptions(providers, connections.data),
      societyProviders.data,
      liveModels,
      new Set(connections.data.map((c) => c.jarvis)),
    );
  }, [catalog.data, connections.data, societyProviders.data, keylessModels.data, liveModels]);
  const seat = seats.find((s) => s.provider.id === providerId) ?? null;
  const accounts = accountChoice(seat);
  const efforts = effortsFor(seat, model);

  const pickSeat = (next: BrainSeat | null) => {
    setProviderId(next?.provider.id ?? "");
    setModel(next ? next.provider.default_model || modelsFor(next)[0]?.id || "" : "");
    setEffort(next?.provider.default_effort ?? "");
    setAccountId("");
  };

  // A fresh dialog starts on the brain marked active, else the first seat —
  // never on a row that is not connected, because none is listed.
  useEffect(() => {
    if (!providerId && seats.length) pickSeat(defaultSeat(seats));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seats, providerId]);

  // A model change can leave the picked effort off that model's ladder.
  useEffect(() => {
    if (efforts.length && !efforts.includes(effort)) setEffort(seat?.provider.default_effort ?? efforts[0]);
    if (!efforts.length && effort) setEffort("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [efforts.join("|")]);

  useEffect(() => {
    if (!open) return;
    setName("");
    setTitle("");
    setDescription("");
    setRecipe(defaultRecipe(DEFAULT_STYLE));
    setProviderId("");
    setModel("");
    setEffort("");
    setAccountId("");
    setImportProblems(null);
    setImportedName(null);
    setError(null);
    setSubmitting(false);
  }, [open]);

  const palette = useMemo(() => resolvePalette(recipe), [recipe]);

  // The look editor's own truth: which figure the recipe names, what
  // archetype it is, and therefore which slots, parts and heights exist. An
  // imported GLB has no catalog row — its rows are colours and height only.
  const imported = Boolean(recipe.model);
  const baseEntry = imported ? null : catalogBaseFor(recipe);
  const archetype: FigureArchetype = baseEntry?.archetype ?? recipe.archetype;
  // Which wardrobe fits this body: a hat cut for one skull is not offered on
  // another, even when the two share a style.
  const family = baseEntry?.family ?? null;
  const styleOptions = useMemo(() => stylesWithBases(), []);
  const bases = useMemo(() => basesForStyle(style), [style]);
  const slots = useMemo(
    () => (imported ? [] : slotsWithParts(archetype, style, family)),
    [imported, archetype, style, family],
  );
  const heights = imported ? IMPORTED_RANGE : (HEIGHT_RANGE[archetype] ?? HEIGHT_RANGE.biped);
  const defaultHeight = baseEntry?.heightM ?? 1.75;
  const heightM = Math.min(heights.max, Math.max(heights.min, recipe.heightM ?? defaultHeight));

  /**
   * Move the recipe onto a base: its archetype comes with it, the parts that
   * the new base and style cannot wear are dropped, and the height lands in
   * the new archetype's band. Carrying a biped's cape onto a fox, or a 1.75 m
   * height onto a 0.9 m animal, is how a "working" creator renders nonsense.
   */
  const selectBase = (nextStyle: string, base: string) => {
    const entry = CATALOG.bases.find((b) => b.base === base) ?? null;
    const nextArchetype: FigureArchetype = entry?.archetype ?? "biped";
    const band = HEIGHT_RANGE[nextArchetype] ?? HEIGHT_RANGE.biped;
    const nextFamily = entry?.family ?? null;
    setStyle(nextStyle);
    setRecipe((r) => {
      const next: FigureRecipe = {
        ...r,
        archetype: nextArchetype,
        base,
        style: nextStyle,
        parts: keepablePartsFor(r.parts, nextArchetype, nextStyle, nextFamily),
        heightM: Math.min(band.max, Math.max(band.min, r.heightM ?? entry?.heightM ?? 1.75)),
        // Colours a person chose are theirs and survive the switch; colours
        // they never touched are the OLD figure's defaults and have no
        // business on the new one — a fox does not want the jeans blue its
        // paws inherited from the casual body.
        palette: untouchedPalette(r) && entry ? { ...entry.palette } : r.palette,
      };
      delete next.model;
      return next;
    });
    setImportedName(null);
  };

  const setCell = (cell: PaletteCell, value: string) =>
    setRecipe((r) => ({ ...r, palette: { ...r.palette, [cell]: value } }));
  const applyPreset = (id: string) => {
    const preset = PALETTE_PRESETS.find((p) => p.id === id);
    if (preset) setRecipe((r) => ({ ...r, palette: { ...preset.palette } }));
  };

  /**
   * Import the person's own GLB: the backend runs the figure gate and keeps
   * the file only when it passes; its reasons come back verbatim otherwise.
   */
  const importFigure = async (file: File) => {
    setImporting(true);
    setImportProblems(null);
    try {
      const res = await fetch(`/api/society/figures?name=${encodeURIComponent(file.name.replace(/\.glb$/i, ""))}`, {
        method: "POST",
        headers: { "Content-Type": "model/gltf-binary" },
        body: file,
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null;
        setImportProblems([typeof detail?.detail === "string" ? detail.detail : t("society.create.import_failed")]);
        return;
      }
      const payload = (await res.json()) as
        | { accepted: true; figure: { url: string; file: string; height_m: number | null } }
        | { accepted: false; problems: string[] };
      if (!payload.accepted) {
        setImportProblems(payload.problems);
        return;
      }
      setImportedName(payload.figure.file);
      setStyle("custom");
      setRecipe((r) => ({ ...r, model: payload.figure.url, parts: {}, style: "custom" }));
    } catch {
      setImportProblems([t("society.create.import_failed")]);
    } finally {
      setImporting(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const submit = async () => {
    if (!name.trim()) {
      setError(t("society.create.name_required"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const agent = await createAgent({
        name,
        title: title.trim() || t("society.create.title_fallback"),
        description,
        figure: recipe,
        palette: { primary: palette.primary, secondary: palette.secondary, accent: palette.accent },
        provider: seat?.provider.id ?? "",
        providerLabel: seat?.provider.label ?? t("society.create.provider_unknown"),
        model,
        effort,
        accountId,
        // Every tool Jarvis has connected; what the agent reaches for first
        // is settled in its own chat afterwards (maintainer, 2026-09-02).
        grantMode: "all",
        toolGrants: [],
        focus: [],
        permissionCeiling: ceiling,
        dailyBudgetUsd: Math.max(0, Number.parseFloat(budget) || 0),
      });
      onCreated(agent.agentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("society.create.error"));
      setSubmitting(false);
    }
  };

  const fieldClass =
    "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong";
  const labelClass = "mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground";

  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="create-agent-dialog"
          className="fixed inset-4 z-50 flex flex-col rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-y-8 lg:inset-x-16"
          onPointerDownOutside={(event) => {
            if (isComboboxPanelEvent(event)) event.preventDefault();
          }}
          onFocusOutside={(event) => {
            if (isComboboxPanelEvent(event)) event.preventDefault();
          }}
          onInteractOutside={(event) => {
            if (isComboboxPanelEvent(event)) event.preventDefault();
          }}
        >
          <header className="flex shrink-0 items-center gap-3 overflow-hidden rounded-t-lg border-b border-border px-5 py-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                {t("society.create.title")}
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted-foreground">
                {t("society.create.subtitle")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              aria-label={t("society.card.close")}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
            >
              <X className="h-4 w-4" aria-hidden />
            </Dialog.Close>
          </header>

          <div className="grid min-h-0 flex-1 overflow-hidden rounded-b-lg grid-cols-[minmax(320px,1fr)_minmax(360px,1.1fr)]">
            {/* ---- left: the three fields + Advanced ---- */}
            <ScrollArea className="min-h-0 border-r border-border">
              <form
                className="flex flex-col gap-4 p-5"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <div>
                  <label htmlFor="society-create-name" className={labelClass}>
                    {t("society.create.name")}
                  </label>
                  <input
                    id="society-create-name"
                    className={fieldClass}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={t("society.create.name_placeholder")}
                    autoComplete="off"
                    autoFocus
                  />
                </div>
                <div>
                  <label htmlFor="society-create-title" className={labelClass}>
                    {t("society.create.title_field")}
                  </label>
                  <input
                    id="society-create-title"
                    className={fieldClass}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={t("society.create.title_placeholder")}
                    autoComplete="off"
                  />
                </div>
                <div>
                  <label htmlFor="society-create-description" className={labelClass}>
                    {t("society.create.description")}
                  </label>
                  <textarea
                    id="society-create-description"
                    className={cn(fieldClass, "min-h-[140px] resize-y leading-relaxed")}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder={t("society.create.description_placeholder")}
                  />
                  <p className="mt-1 text-xs text-muted-foreground">{t("society.create.description_hint")}</p>
                </div>

                {/* ---- Runs on: only what is connected on this machine ---- */}
                <div data-testid="society-create-brain">
                  <span className={labelClass}>{t("society.create.brain")}</span>
                  {seatsLoading && seats.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("society.create.catalog_loading")}</p>
                  ) : seats.length === 0 ? (
                    <p
                      data-testid="society-create-nothing-connected"
                      className="rounded-md border border-dashed border-border px-3 py-2 text-xs leading-relaxed text-muted-foreground"
                    >
                      {t("society.create.nothing_connected")}
                    </p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      <Combobox
                        value={seat?.provider.id ?? ""}
                        groups={seatGroups(seats, t)}
                        onChange={(id) => pickSeat(seats.find((s) => s.provider.id === id) ?? null)}
                        ariaLabel={t("society.create.brain")}
                        testId="society-create-provider"
                      />
                      {accounts.length > 0 ? (
                        <div>
                          <span className={labelClass}>{t("society.create.account")}</span>
                          <Combobox
                            value={accountId}
                            groups={[
                              {
                                id: "accounts",
                                options: [
                                  { value: "", label: t("society.create.account_active") },
                                  ...accounts.map((a) => ({
                                    value: a.id,
                                    label: a.label,
                                    hint: accountHint(a),
                                  })),
                                ],
                              },
                            ]}
                            onChange={setAccountId}
                            ariaLabel={t("society.create.account")}
                          />
                        </div>
                      ) : null}
                      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                        <div className="min-w-0">
                          <label htmlFor="society-create-model" className={labelClass}>
                            {t("society.create.model")}
                          </label>
                          {modelsFor(seat).length > 0 ? (
                            <Combobox
                              id="society-create-model"
                              value={model}
                              groups={[
                                {
                                  id: "models",
                                  options: modelsFor(seat).map((m) => ({
                                    value: m.id,
                                    label: m.label,
                                    hint: m.note,
                                  })),
                                },
                              ]}
                              onChange={setModel}
                              ariaLabel={t("society.create.model")}
                            />
                          ) : (
                            <input
                              id="society-create-model"
                              className={cn(fieldClass, "font-mono")}
                              value={model}
                              onChange={(e) => setModel(e.target.value)}
                              placeholder={t("society.create.model_placeholder")}
                              autoComplete="off"
                            />
                          )}
                        </div>
                        {efforts.length > 0 ? (
                          <div>
                            <span className={labelClass}>{t("society.create.effort")}</span>
                            <Combobox
                              value={effort}
                              groups={[
                                {
                                  id: "efforts",
                                  options: efforts.map((lvl) => ({ value: lvl, label: effortLabel(lvl, t) })),
                                },
                              ]}
                              onChange={setEffort}
                              ariaLabel={t("society.create.effort")}
                            />
                          </div>
                        ) : null}
                      </div>
                    </div>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">{t("society.create.brain_hint")}</p>
                </div>

                <Collapsible.Root open={advanced} onOpenChange={setAdvanced}>
                  <Collapsible.Trigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-secondary"
                    >
                      {t("society.create.advanced")}
                      <ChevronDown
                        className={cn("h-4 w-4 transition-transform", advanced && "rotate-180")}
                        aria-hidden
                      />
                    </button>
                  </Collapsible.Trigger>
                  <Collapsible.Content className="flex flex-col gap-4 pt-4">
                    <div>
                      <span className={labelClass}>{t("society.create.ceiling")}</span>
                      <Segmented
                        value={ceiling}
                        options={CEILINGS.map((c) => ({ value: c, label: t(`society.ceiling.${c}`) }))}
                        onChange={(v) => setCeiling(v as PermissionCeiling)}
                      />
                    </div>
                    <div>
                      <label htmlFor="society-create-budget" className={labelClass}>
                        {t("society.create.budget")}
                      </label>
                      <input
                        id="society-create-budget"
                        type="number"
                        min={0}
                        step={0.5}
                        inputMode="decimal"
                        className={cn(fieldClass, "w-32 font-mono")}
                        value={budget}
                        onChange={(e) => setBudget(e.target.value)}
                      />
                    </div>
                  </Collapsible.Content>
                </Collapsible.Root>

                {error ? (
                  <p role="alert" className="text-xs text-destructive">
                    {error}
                  </p>
                ) : null}
                <div className="flex items-center justify-end gap-2 pt-2">
                  <Button type="button" variant="ghost" size="sm" onClick={onClose}>
                    {t("society.create.cancel")}
                  </Button>
                  <Button type="submit" size="sm" disabled={submitting} data-testid="society-create-submit">
                    {t("society.create.submit")}
                  </Button>
                </div>
              </form>
            </ScrollArea>

            {/* ---- right: the character, live ---- */}
            <div className="flex min-h-0 flex-col">
              <div className="society-figure-column relative min-h-[240px] flex-1">
                <AgentFigureViewer recipe={recipe} quiet />
              </div>
              <div className="shrink-0 border-t border-border p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className={labelClass}>{t("society.create.look")}</span>
                  <div className="flex items-center gap-1">
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".glb,model/gltf-binary"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void importFigure(file);
                    }}
                  />
                  <button
                    type="button"
                    disabled={importing}
                    onClick={() => fileInput.current?.click()}
                    title={t("society.create.import_hint")}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-50"
                  >
                    <Upload className="h-3.5 w-3.5" aria-hidden />
                    {importing ? t("society.create.importing") : t("society.create.import")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setRecipe((r) => ({ ...r, palette: shufflePalette() }))}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
                  >
                    <Shuffle className="h-3.5 w-3.5" aria-hidden />
                    {t("society.create.shuffle")}
                  </button>
                  </div>
                </div>
                {importProblems ? (
                  <div role="alert" className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-foreground">
                    <p className="mb-1 font-medium">{t("society.create.import_rejected")}</p>
                    <ul className="list-disc pl-4">
                      {importProblems.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {importedName && recipe.model ? (
                  <p className="mb-3 text-xs text-muted-foreground">
                    {t("society.create.import_ok").replace("{0}", importedName)}{" "}
                    <button
                      type="button"
                      className="underline hover:text-foreground"
                      onClick={() => selectBase(DEFAULT_STYLE, basesForStyle(DEFAULT_STYLE)[0]?.base ?? "rogue")}
                    >
                      {t("society.create.import_reset")}
                    </button>
                  </p>
                ) : null}
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <span className="w-16 text-xs text-muted-foreground">{t("society.create.style")}</span>
                    <Segmented
                      value={style}
                      options={Object.keys(CATALOG.styles)
                        .filter((id) => !isReservedStyle(id))
                        .map((id) => {
                        // "Imported" is not a style to pick — it is where the
                        // Import button lands, so it lights up once a GLB is in.
                        const live = id === "custom" ? imported : styleOptions.includes(id);
                        return {
                          value: id,
                          label: t(`society.style.${id}`),
                          disabled: !live,
                          hint: live
                            ? undefined
                            : t(id === "custom" ? "society.create.style_import_hint" : "society.create.style_soon"),
                        };
                      })}
                      onChange={(next) => {
                        if (next === style) return;
                        const keep = basesForStyle(next).some((b) => b.base === recipe.base);
                        selectBase(next, keep ? recipe.base : (basesForStyle(next)[0]?.base ?? recipe.base));
                      }}
                    />
                  </div>
                  {imported ? null : (
                    <div className="flex items-center gap-3">
                      <span className="w-16 text-xs text-muted-foreground">{t("society.create.base")}</span>
                      <Segmented
                        value={recipe.base}
                        options={bases.map((b) => ({ value: b.base, label: b.label }))}
                        onChange={(base) => selectBase(style, base)}
                      />
                    </div>
                  )}
                  {slots.map((slot) => (
                    <div key={slot} className="flex items-center gap-3">
                      <span className="w-16 text-xs text-muted-foreground">{t(`society.slot.${slot}`)}</span>
                      <div className="flex flex-wrap gap-1">
                        <Segmented
                          value={recipe.parts[slot] ?? ""}
                          options={[
                            { value: "", label: t("society.create.none") },
                            ...partsForSlot(slot, archetype, style, family).map((part) => ({
                              value: part.id,
                              label: part.label,
                            })),
                          ]}
                          onChange={(id) =>
                            setRecipe((r) => {
                              const parts = { ...r.parts };
                              if (id) parts[slot] = id;
                              else delete parts[slot];
                              return { ...r, parts };
                            })
                          }
                        />
                      </div>
                    </div>
                  ))}
                  <div className="flex items-center gap-3">
                    <span className="w-16 text-xs text-muted-foreground">{t("society.create.presets")}</span>
                    <div className="flex flex-wrap gap-1.5">
                      {PALETTE_PRESETS.map((preset) => {
                        const p = resolvePalette({ palette: preset.palette });
                        return (
                          <button
                            key={preset.id}
                            type="button"
                            title={t(`society.presets.${preset.labelKey}`)}
                            aria-label={t(`society.presets.${preset.labelKey}`)}
                            onClick={() => applyPreset(preset.id)}
                            className="flex h-7 items-center gap-0.5 rounded-md border border-border px-1.5 hover:bg-secondary"
                          >
                            <span className="h-3.5 w-3.5 rounded-sm" style={{ background: p.primary }} />
                            <span className="h-3.5 w-3.5 rounded-sm" style={{ background: p.secondary }} />
                            <span className="h-3.5 w-3.5 rounded-sm" style={{ background: p.accent }} />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div className="flex items-start gap-3">
                    <span className="w-16 pt-1 text-xs text-muted-foreground">{t("society.create.colours")}</span>
                    <div className="grid flex-1 grid-cols-3 gap-x-3 gap-y-1.5 sm:grid-cols-6">
                      {EDITABLE_CELLS.map((cell) => (
                        <label key={cell} className="flex flex-col items-start gap-0.5 text-xs text-muted-foreground">
                          <input
                            type="color"
                            value={palette[cell]}
                            onChange={(e) => setCell(cell, e.target.value)}
                            aria-label={t(`society.cell.${cell}`)}
                            className="h-6 w-full cursor-pointer rounded border border-border bg-transparent p-0"
                          />
                          {t(`society.cell.${cell}`)}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <label htmlFor="society-create-height" className="w-16 text-xs text-muted-foreground">
                      {t("society.create.height")}
                    </label>
                    <input
                      id="society-create-height"
                      type="range"
                      min={heights.min}
                      max={heights.max}
                      step={0.01}
                      value={heightM}
                      onChange={(e) => setRecipe((r) => ({ ...r, heightM: Number.parseFloat(e.target.value) }))}
                      className="flex-1"
                    />
                    <span className="w-14 text-right font-mono text-xs text-muted-foreground">
                      {heightM.toFixed(2)} m
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const KIND_KEYS: Record<BrainKind, string> = {
  subscription: "society.create.kind_subscription",
  api: "society.create.kind_api",
  local: "society.create.kind_local",
};

/**
 * The "Runs on" list: one group per kind, subscriptions first. A row wears
 * its real mark (the IDE's for a CLI seat, the provider family's for an API
 * row) and says what it bills — the one signed-in login's e-mail on a
 * subscription, else the kind.
 */
function seatGroups(seats: BrainSeat[], t: (key: string) => string): ComboboxGroup[] {
  const groups: ComboboxGroup[] = [];
  for (const kind of ["subscription", "api", "local"] as const) {
    const rows = seats.filter((s) => s.kind === kind);
    if (!rows.length) continue;
    groups.push({
      id: kind,
      label: t(KIND_KEYS[kind]),
      options: rows.map((s) => {
        const p = s.provider;
        const only = s.accounts.length === 1 ? accountHint(s.accounts[0]) : "";
        return {
          value: p.id,
          label: p.label,
          hint: only || t(KIND_KEYS[kind]),
          searchText: `${p.family} ${p.runner} ${s.accounts.map((a) => a.email ?? "").join(" ")}`,
          icon: p.agentMark ? (
            <AgentMark agent={p.agentMark} label={p.label} logoUrl={p.logoUrl} variant="plain" size="sm" />
          ) : (
            <ProviderLogo providerId={p.id} label={p.label} size="sm" />
          ),
        };
      }),
    });
  }
  return groups;
}

/**
 * The live model lists of `ids`, fetched together; a provider that answers
 * nothing (not running, nothing installed, route missing) maps to [].
 */
async function fetchModelLists(ids: string[]): Promise<Record<string, CuratedModel[]>> {
  const lists = await Promise.all(
    ids.map(async (id) => {
      const rows = await fetchProviderModels(id).catch(() => []);
      return [id, rows.map((m) => ({ id: m.id, label: m.label ?? m.name ?? m.id }))] as const;
    }),
  );
  return Object.fromEntries(lists);
}

interface SegmentedOption {
  value: string;
  label: string;
  disabled?: boolean;
  hint?: string;
}

function Segmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: SegmentedOption[];
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" className="inline-flex rounded-md border border-border bg-background p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          disabled={option.disabled}
          title={option.hint}
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
            option.value === value ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
