import { useMemo } from "react";
import { AgentMark } from "@/components/agentic/AgentMark";
import { isUnguardedPermissionMode, permissionModeIcon } from "@/components/agentchat/permissionIcons";
import { Combobox, type ComboboxGroup, type ComboboxOption } from "@/components/ui/combobox";
import { useT } from "@/i18n";
import type { CuratedModel } from "@/lib/agentChatApi";
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

const SEP = "\u0000";

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
  const olderLabel = useT()("agent_chat.older_models");
  const groups = useMemo<ComboboxGroup[]>(() => providers.map((provider) => {
    const locked = lockedProvider !== null && provider.id !== lockedProvider;
    const disabled = !provider.connected || locked;
    const hint = locked ? "new thread" : !provider.connected ? (provider.cli_installed === false ? "not installed" : "connect first") : undefined;
    const icon = <AgentMark agent={provider.agent ?? ""} label={provider.label} logoUrl={provider.logoUrl} variant="plain" size="sm" />;
    const models = modelsOf(provider, liveModels).filter((model) => model.id);
    const toOption = (model: CuratedModel): ComboboxOption => ({
      value: `${provider.id}${SEP}${model.id}`,
      label: model.label || model.id,
      hint: hint ?? model.note,
      searchText: `${model.id} ${agentName(provider)}`,
      icon,
      disabled,
    });
    // Newest of each model line first; earlier versions fold away.
    const ranked = rankModels(models);
    return {
      id: provider.id,
      label: agentName(provider),
      options: [
        { value: `${provider.id}${SEP}`, label: agentName(provider), hint: hint ?? "default model", icon, disabled },
        ...ranked.current.map(toOption),
      ],
      more: ranked.older.length ? { label: olderLabel, options: ranked.older.map(toOption) } : undefined,
    };
  }), [providers, liveModels, lockedProvider, olderLabel]);
  return <Combobox ariaLabel="Coding agent and model" testId="thread-model-picker"
    value={`${draft.provider}${SEP}${draft.model}`} groups={groups}
    onChange={(value) => { const [provider, model = ""] = value.split(SEP); onPick(provider, model); }}
    fallbackLabel={draft.model || (providers.length ? "Choose an agent" : "Loading agents…")} searchPlaceholder="Search agents and models"
    triggerHint={false} className={TRIGGER} />;
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
