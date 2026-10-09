import { useMemo } from "react";
import { brainValue, splitBrainValue } from "@/components/agentchat/AgentComposer";
import { ComposerBrainPicker, type BrainSection } from "@/components/agentchat/ComposerBrainPicker";
import { AgentMark } from "@/components/agentic/AgentMark";
import { isUnguardedPermissionMode, permissionModeIcon } from "@/components/agentchat/permissionIcons";
import { Combobox, type ComboboxGroup, type ComboboxOption } from "@/components/ui/combobox";
import { useT } from "@/i18n";
import { isApiRunner, type CuratedModel } from "@/lib/agentChatApi";
import { offeredModels, useSavedHiddenModels } from "@/lib/agentProviderPrefs";
import { effortLadder } from "@/lib/effortLadder";
import { rankModels } from "@/lib/modelRanking";
import { cn } from "@/lib/utils";
import type { ComposerDraft, ProviderOption } from "@/store/agentChat";

/**
 * The three picks under a thread's composer: which coding agent and model
 * answer, how hard it thinks, and how much it may do without asking. Quiet
 * text buttons until opened; each opens the app's own searchable list.
 */

/** One quiet control in the composer's toolbar: 28 px, medium weight, a lift on hover. */
const TRIGGER = "h-7 w-auto max-w-[240px] gap-1.5 rounded-lg bg-transparent px-2.5 py-0 text-sm font-medium text-foreground-secondary hover:bg-secondary hover:text-foreground";

/** The hairline between two toolbar controls. */
function Separator() {
  return <span aria-hidden className="mx-0.5 hidden h-4 w-px shrink-0 bg-border sm:block" />;
}

/** The name a coding agent goes by, not its API brand ("Claude Code", not "Anthropic Claude"). */
export function agentName(provider: Pick<ProviderOption, "agent" | "label">): string {
  switch (provider.agent) {
    case "claude": return "Claude Code";
    case "codex": return "Codex";
    default: return provider.label;
  }
}

const EFFORTS = new Set(["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]);

/** An effort level in the chat's own words ("" is the agent's default). */
function effortLabel(level: string, t: (key: string) => string): string {
  if (!level) return t("agent_chat.effort_default");
  return EFFORTS.has(level) ? t(`agent_chat.effort_${level}`) : level;
}

/** The models a provider row offers: its live list when it has one, else the curated one. */
export function modelsOf(provider: ProviderOption, liveModels: Record<string, CuratedModel[]>): CuratedModel[] {
  const live = liveModels[provider.id];
  return provider.models_source === "live" && live && live.length ? live : provider.curated_models ?? [];
}

export function AgentModelPicker({
  providers,
  liveModels,
  draft,
  lockedProvider,
  onPick,
}: {
  providers: ProviderOption[];
  liveModels: Record<string, CuratedModel[]>;
  draft: ComposerDraft;
  /** A thread that has started keeps its coding agent; only the model can change. */
  lockedProvider: string | null;
  onPick: (provider: string, model: string) => void;
}) {
  const t = useT();
  const olderLabel = t("agent_chat.older_models");
  const defaultLabel = t("agent_chat.model_default");
  const saved = useSavedHiddenModels((state) => state.hidden);
  const groups = useMemo<ComboboxGroup[]>(() => providers.map((provider) => {
    const locked = lockedProvider !== null && provider.id !== lockedProvider;
    const disabled = !provider.connected || locked;
    const hint = locked ? "new thread" : !provider.connected ? (provider.cli_installed === false ? "not installed" : "connect first") : undefined;
    const icon = <AgentMark agent={provider.agent ?? ""} label={provider.label} logoUrl={provider.logoUrl} variant="plain" size="sm" />;
    // Whose model a row is, on a quiet second line under its name.
    const owner = (note?: string) => <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="inline-flex shrink-0 scale-[0.86]">{icon}</span>
      <span className="truncate">{note ? `${agentName(provider)} · ${note}` : agentName(provider)}</span>
    </span>;
    // Models switched off on the API Keys page stay out; the current pick stays.
    const current = provider.id === draft.provider ? draft.model : "";
    const models = offeredModels(provider, modelsOf(provider, liveModels), current, saved).filter((model) => model.id);
    const toOption = (model: CuratedModel): ComboboxOption => ({
      value: brainValue(provider.id, model.id),
      label: model.label || model.id,
      description: owner(model.note),
      hint,
      searchText: `${model.id} ${agentName(provider)}`,
      disabled,
    });
    // Newest of each model line first; earlier versions fold away.
    const ranked = rankModels(models);
    return {
      id: provider.id,
      label: agentName(provider),
      options: [
        { value: brainValue(provider.id, ""), label: defaultLabel, triggerLabel: agentName(provider), description: owner(), hint, disabled, searchText: agentName(provider) },
        ...ranked.current.map(toOption),
      ],
      more: ranked.older.length ? { label: olderLabel, options: ranked.older.map(toOption) } : undefined,
    };
  }), [providers, liveModels, lockedProvider, draft.provider, draft.model, saved, olderLabel, defaultLabel]);
  // The rail on the panel's left: one mark per coding agent, plus favourites.
  const sections = useMemo<BrainSection[]>(() => providers.map((provider) => ({
    id: provider.id,
    family: provider.family,
    access: provider.keyless ? "local" : isApiRunner(provider.runner) ? "api" : "subscription",
    label: agentName(provider),
    icon: <AgentMark agent={provider.agent ?? ""} label={provider.label} logoUrl={provider.logoUrl} variant="plain" size="sm" />,
    muted: !provider.connected || (lockedProvider !== null && provider.id !== lockedProvider),
  })), [providers, lockedProvider]);
  const pickedIcon = providers.find((provider) => provider.id === draft.provider);
  return <ComposerBrainPicker testId="thread-model-picker" ariaLabel="Coding agent and model"
    value={brainValue(draft.provider, draft.model)} groups={groups} sections={sections} currentSection={draft.provider}
    onChange={(value) => { const [provider, model] = splitBrainValue(value); onPick(provider, model); }}
    fallbackLabel={draft.model || (providers.length ? "Choose an agent" : "Loading agents…")} searchPlaceholder="Search agents and models"
    triggerPrefix={pickedIcon ? <AgentMark agent={pickedIcon.agent ?? ""} label={pickedIcon.label} logoUrl={pickedIcon.logoUrl} variant="plain" size="sm" /> : undefined} />;
}

export function EffortPicker({ provider, draft, liveModels, onPick, separated = false }: {
  separated?: boolean;
  provider: ProviderOption | null;
  draft: ComposerDraft;
  liveModels: Record<string, CuratedModel[]>;
  onPick: (effort: string) => void;
}) {
  const t = useT();
  const levels = useMemo(
    () => (provider ? effortLadder(provider, modelsOf(provider, liveModels), draft.model) : []),
    [provider, liveModels, draft.model],
  );
  if (levels.length === 0 || (levels.length === 1 && levels[0] === "")) return null;
  return <>
    {separated && <Separator />}
    <Combobox ariaLabel="Reasoning effort" testId="thread-effort-picker" value={draft.effort}
      groups={[{ id: "effort", options: levels.map((level) => ({ value: level, label: effortLabel(level, t) })) }]}
      onChange={onPick} fallbackLabel={effortLabel(draft.effort, t)} triggerHint={false} className={TRIGGER} />
  </>;
}

export function AccessPicker({ provider, draft, onPick, separated = false }: {
  separated?: boolean;
  provider: ProviderOption | null;
  draft: ComposerDraft;
  onPick: (mode: string) => void;
}) {
  const modes = provider?.permission_modes ?? [];
  if (modes.length === 0) return null;
  return <>
    {separated && <Separator />}
    <Combobox ariaLabel="What the agent may do without asking" testId="thread-access-picker" value={draft.permissionMode}
      groups={[{
        id: "access",
        label: "Permissions",
        options: modes.map((mode) => {
          const Icon = permissionModeIcon(mode.id);
          const unguarded = isUnguardedPermissionMode(mode.id);
          // The description is a second line under the name, never a right-hand
          // hint: a sentence-long hint pushed the name out of a narrow list.
          return {
            value: mode.id,
            label: mode.label,
            searchText: mode.description,
            description: mode.description,
            icon: <Icon aria-hidden className={cn("h-4 w-4 shrink-0", unguarded ? "text-warning" : "text-muted-foreground")} />,
          };
        }),
      }]}
      onChange={onPick} fallbackLabel={draft.permissionMode || "Access"} triggerHint={false} panelMinWidth={340}
      className={cn(TRIGGER, isUnguardedPermissionMode(draft.permissionMode) && "text-warning hover:text-warning")} />
  </>;
}
