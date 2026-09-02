/**
 * Create an agent: the left column is what a person actually decides, the
 * character on the right — live, turnable, from above and below — with the
 * look editor under it (MASTERPLAN §4.2, agent-definition §6: no wizard).
 *
 * Left, top to bottom (maintainer, 2026-09-02, modelled on Grok Bot's "New
 * Bot" sheet): name, title, description; "Runs on" — ONLY the seats that are
 * connected on this machine, subscriptions first (a CLI on a plan bills the
 * plan, not per token), then saved API keys, then local models, with the
 * login to use when a CLI has more than one, the model and the effort; a
 * visible "Focus" chip row (what the agent reaches for first — everything
 * Jarvis has connected stays available); and a "More" disclosure for the
 * permission ceiling, the tool-access mode and the daily budget. A provider
 * that is not connected is not listed: one sentence says where to connect it.
 * Everything else the agent is told in its own chat afterwards.
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
import { Combobox, type ComboboxGroup } from "@/components/ui/combobox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import {
  fetchAgentChatCatalog,
  fetchAgentConnections,
  type AgentConnectionRow,
} from "@/lib/agentChatApi";
import { fetchSocietyProviders, type SocietyProviderRow } from "@/lib/societyApi";
import { cn } from "@/lib/utils";
import { joinProviderOptions } from "@/store/agentChat";

import { CapabilityChip } from "../CapabilityChip";
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
import {
  useCreateAgent,
  useSocietyCapabilities,
  type Capability,
  type GrantMode,
  type PermissionCeiling,
} from "../data";
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
import { basesForStyle, partsForSlot, slotsWithParts, stylesWithBases, CATALOG } from "../figures/figureRegistry";

const CEILINGS: readonly PermissionCeiling[] = ["safe", "monitor", "ask"];
const GRANTS: readonly GrantMode[] = ["all", "allowlist"];
const HEIGHT_MIN = 1.5;
const HEIGHT_MAX = 2.1;

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
  const [recipe, setRecipe] = useState<FigureRecipe>(() => defaultRecipe("ranger"));
  const [style, setStyle] = useState<string>("modern");
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [accountId, setAccountId] = useState("");
  const [ceiling, setCeiling] = useState<PermissionCeiling>("monitor");
  const [grantMode, setGrantMode] = useState<GrantMode>("all");
  const [budget, setBudget] = useState("2");
  const [focus, setFocus] = useState<string[]>([]);
  const [toolQuery, setToolQuery] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importProblems, setImportProblems] = useState<string[] | null>(null);
  const [importedName, setImportedName] = useState<string | null>(null);
  const capabilities = useSocietyCapabilities(open);
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
  const seatsLoading = catalog.isLoading || connections.isLoading || societyProviders.isLoading;
  // Joined only once all three answers are in (each query settles to [] on
  // a failure): a join over a catalog without the credential rows would call
  // every API seat unconnected, list the local rows alone, and the default
  // pick would land on one of them before the keys arrive.
  const seats = useMemo<BrainSeat[]>(() => {
    const providers = catalog.data?.providers ?? [];
    if (!providers.length || !connections.data || !societyProviders.data) return [];
    return brainSeats(joinProviderOptions(providers, connections.data), societyProviders.data);
  }, [catalog.data, connections.data, societyProviders.data]);
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
    setRecipe(defaultRecipe("ranger"));
    setProviderId("");
    setModel("");
    setEffort("");
    setAccountId("");
    setGrantMode("all");
    setFocus([]);
    setToolQuery("");
    setImportProblems(null);
    setImportedName(null);
    setError(null);
    setSubmitting(false);
  }, [open]);

  const palette = useMemo(() => resolvePalette(recipe), [recipe]);

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
        grantMode,
        // "Only the highlighted tools": the focus list IS the allow-list.
        toolGrants: grantMode === "allowlist" ? focus : [],
        focus,
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
    "w-full rounded-md border border-border bg-background px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong";
  const labelClass = "mb-1 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground";

  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="create-agent-dialog"
          className="fixed inset-4 z-50 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-y-8 lg:inset-x-16"
        >
          <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
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

          <div className="grid min-h-0 flex-1 grid-cols-[minmax(320px,1fr)_minmax(360px,1.1fr)]">
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
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("society.create.description_hint")}</p>
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
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("society.create.brain_hint")}</p>
                </div>

                {/* ---- Focus: what the agent reaches for first ---- */}
                <div data-testid="society-create-focus">
                  <span className={labelClass}>{t("society.create.focus_title")}</span>
                  <p className="mb-2 text-[11px] text-muted-foreground">
                    {grantMode === "allowlist" ? t("society.create.allowlist_hint") : t("society.create.focus_hint")}
                  </p>
                  <input
                    type="search"
                    value={toolQuery}
                    onChange={(e) => setToolQuery(e.target.value)}
                    placeholder={t("society.create.tools_search")}
                    aria-label={t("society.create.tools_search")}
                    className={cn(fieldClass, "mb-2")}
                  />
                  {capabilities.isLoading ? (
                    <p className="text-xs text-muted-foreground">{t("society.create.tools_loading")}</p>
                  ) : capabilities.isError || (capabilities.data ?? []).length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("society.create.tools_unavailable")}</p>
                  ) : (
                    <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                      {filterCapabilities(capabilities.data ?? [], toolQuery).map((cap) => {
                        const on = focus.includes(cap.id);
                        return (
                          <CapabilityChip
                            key={cap.id}
                            id={cap.id}
                            capability={cap}
                            selected={on}
                            disconnectedHint={t("society.card.not_connected")}
                            onClick={() => setFocus(on ? focus.filter((x) => x !== cap.id) : [...focus, cap.id])}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>

                <Collapsible.Root open={advanced} onOpenChange={setAdvanced}>
                  <Collapsible.Trigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-[13px] font-medium text-foreground hover:bg-secondary"
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
                      <span className={labelClass}>{t("society.create.grant_mode")}</span>
                      <Segmented
                        value={grantMode}
                        options={GRANTS.map((g) => ({ value: g, label: t(`society.grant.${g}`) }))}
                        onChange={(v) => setGrantMode(v as GrantMode)}
                      />
                      <p className="mt-1 text-[11px] text-muted-foreground">{t("society.create.grant_mode_hint")}</p>
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
                  <div role="alert" className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-foreground">
                    <p className="mb-1 font-medium">{t("society.create.import_rejected")}</p>
                    <ul className="list-disc pl-4">
                      {importProblems.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {importedName && recipe.model ? (
                  <p className="mb-3 text-[11px] text-muted-foreground">
                    {t("society.create.import_ok").replace("{0}", importedName)}{" "}
                    <button
                      type="button"
                      className="underline hover:text-foreground"
                      onClick={() => {
                        setImportedName(null);
                        setStyle("modern");
                        setRecipe((r) => {
                          const next = { ...r, style: "modern" };
                          delete next.model;
                          return next;
                        });
                      }}
                    >
                      {t("society.create.import_reset")}
                    </button>
                  </p>
                ) : null}
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <span className="w-16 text-[11px] text-muted-foreground">{t("society.create.style")}</span>
                    <Segmented
                      value={style}
                      options={Object.keys(CATALOG.styles).map((id) => ({
                        value: id,
                        label: t(`society.style.${id}`),
                        disabled: !stylesWithBases().includes(id),
                        hint: stylesWithBases().includes(id) ? undefined : t("society.create.style_soon"),
                      }))}
                      onChange={(next) => {
                        setStyle(next);
                        const first = basesForStyle(next)[0];
                        if (first && !basesForStyle(next).some((b) => b.base === recipe.base)) {
                          setRecipe((r) => ({ ...r, base: first.base, parts: {}, style: next }));
                        }
                      }}
                    />
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="w-16 text-[11px] text-muted-foreground">{t("society.create.base")}</span>
                    <Segmented
                      value={recipe.base}
                      options={basesForStyle(style).map((b) => ({ value: b.base, label: b.label }))}
                      onChange={(base) => setRecipe((r) => ({ ...r, base, style }))}
                    />
                  </div>
                  {slotsWithParts("biped").map((slot) => (
                    <div key={slot} className="flex items-center gap-3">
                      <span className="w-16 text-[11px] text-muted-foreground">{t(`society.slot.${slot}`)}</span>
                      <div className="flex flex-wrap gap-1">
                        <Segmented
                          value={recipe.parts[slot] ?? ""}
                          options={[
                            { value: "", label: t("society.create.none") },
                            ...partsForSlot(slot, "biped", null).map((part) => ({ value: part.id, label: part.label })),
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
                    <span className="w-16 text-[11px] text-muted-foreground">{t("society.create.presets")}</span>
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
                    <span className="w-16 pt-1 text-[11px] text-muted-foreground">{t("society.create.colours")}</span>
                    <div className="grid flex-1 grid-cols-3 gap-x-3 gap-y-1.5 sm:grid-cols-6">
                      {EDITABLE_CELLS.map((cell) => (
                        <label key={cell} className="flex flex-col items-start gap-0.5 text-[10px] text-muted-foreground">
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
                    <label htmlFor="society-create-height" className="w-16 text-[11px] text-muted-foreground">
                      {t("society.create.height")}
                    </label>
                    <input
                      id="society-create-height"
                      type="range"
                      min={HEIGHT_MIN}
                      max={HEIGHT_MAX}
                      step={0.01}
                      value={recipe.heightM ?? 1.75}
                      onChange={(e) => setRecipe((r) => ({ ...r, heightM: Number.parseFloat(e.target.value) }))}
                      className="flex-1"
                    />
                    <span className="w-14 text-right font-mono text-[11px] text-muted-foreground">
                      {(recipe.heightM ?? 1.75).toFixed(2)} m
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

/** Connected first, then by label; a query narrows by label, id and one-liner. */
function filterCapabilities(rows: Capability[], query: string): Capability[] {
  const q = query.trim().toLowerCase();
  return rows
    .filter((c) => !q || `${c.label} ${c.id} ${c.one_liner}`.toLowerCase().includes(q))
    .sort((a, b) => Number(b.connected) - Number(a.connected) || a.label.localeCompare(b.label))
    .slice(0, 80);
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
