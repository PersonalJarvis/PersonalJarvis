/**
 * Create an agent: three fields and an Advanced disclosure on the left, the
 * character on the right — live, turnable, from above and below — with the
 * look editor under it (MASTERPLAN §4.2, agent-definition §6: no wizard).
 *
 * Everything a person changes is visible in the preview the same frame: a
 * preset, a colour, the build, the height. What is not built yet is shown
 * disabled with the reason (the small/large builds), never hidden.
 *
 * Until the society backend is bound, "Create" appends a sample row to this
 * window's roster (data.ts, the single swap point) and opens its card.
 */
import { useEffect, useMemo, useState } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Shuffle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { fetchAgentChatCatalog, type AgentChatProvider } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";

import { useCreateAgent, type GrantMode, type PermissionCeiling } from "../data";
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
import { AVAILABLE_BASES } from "../figures/figureRegistry";

const BASES = ["small", "medium", "large"] as const;
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
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [ceiling, setCeiling] = useState<PermissionCeiling>("monitor");
  const [grantMode, setGrantMode] = useState<GrantMode>("all");
  const [budget, setBudget] = useState("2");
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The same catalog the typed chat offers — API families, CLI seats, local
  // models — filtered by the society surface on the backend.
  const catalog = useQuery({
    queryKey: ["agent-chat", "catalog", "society"],
    queryFn: () => fetchAgentChatCatalog("society"),
    enabled: open,
    staleTime: 60_000,
  });
  const providers: AgentChatProvider[] = catalog.data?.providers ?? [];
  const provider = providers.find((p) => p.id === providerId) ?? providers[0] ?? null;

  useEffect(() => {
    if (!providerId && provider) {
      setProviderId(provider.id);
      setModel(provider.default_model || provider.curated_models[0]?.id || "");
    }
  }, [provider, providerId]);

  useEffect(() => {
    if (!open) return;
    setName("");
    setTitle("");
    setDescription("");
    setRecipe(defaultRecipe("ranger"));
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
        provider: provider?.id ?? "",
        providerLabel: provider?.label ?? t("society.create.provider_unknown"),
        model,
        grantMode,
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
                      <span className={labelClass}>{t("society.create.provider")}</span>
                      {providers.length > 0 ? (
                        <Combobox
                          value={provider?.id ?? ""}
                          groups={[
                            {
                              id: "providers",
                              options: providers.map((p) => ({ value: p.id, label: p.label })),
                            },
                          ]}
                          onChange={(id) => {
                            setProviderId(id);
                            const next = providers.find((p) => p.id === id);
                            setModel(next?.default_model || next?.curated_models[0]?.id || "");
                          }}
                          ariaLabel={t("society.create.provider")}
                        />
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          {catalog.isLoading ? t("society.create.catalog_loading") : t("society.create.provider_unknown")}
                        </p>
                      )}
                    </div>
                    <div>
                      <label htmlFor="society-create-model" className={labelClass}>
                        {t("society.create.model")}
                      </label>
                      {provider && provider.curated_models.length > 0 ? (
                        <Combobox
                          id="society-create-model"
                          value={model}
                          groups={[
                            {
                              id: "models",
                              options: provider.curated_models.map((m) => ({
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
                  <button
                    type="button"
                    onClick={() => setRecipe((r) => ({ ...r, palette: shufflePalette() }))}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
                  >
                    <Shuffle className="h-3.5 w-3.5" aria-hidden />
                    {t("society.create.shuffle")}
                  </button>
                </div>
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <span className="w-16 text-[11px] text-muted-foreground">{t("society.create.base")}</span>
                    <Segmented
                      value={recipe.base}
                      options={BASES.map((b) => ({
                        value: b,
                        label: t(`society.base.${b}`),
                        disabled: !AVAILABLE_BASES.biped.includes(b),
                        hint: AVAILABLE_BASES.biped.includes(b) ? undefined : t("society.create.base_soon"),
                      }))}
                      onChange={(base) => setRecipe((r) => ({ ...r, base }))}
                    />
                  </div>
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
