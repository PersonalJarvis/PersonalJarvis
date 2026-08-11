/**
 * Allocate terminal slots by agent instead of editing every pane separately.
 * The backend still receives its existing one-entry-per-terminal plan.
 */
import { useState } from "react";
import { Check, Minus, Pencil, Plus, Trash2 } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { AgentAccount } from "@/lib/agentAccountsApi";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import {
  deleteCustomCli,
  fetchCustomClis,
  type CustomCli,
} from "@/lib/workspaceClisApi";
import { AgentMark } from "./AgentMark";
import { Button, IconButton, SectionLabel } from "./controls";
import { CustomCliDialog } from "./CustomCliDialog";
import { BrandedSelect } from "@/components/ui/select";

export interface PlannedTerminal {
  agent: string;
  name: string;
  /** Which subscription of `agent` this pane opens on. */
  account?: string;
}

type Counts = Record<string, number>;

export interface AgentAllocationProps {
  planned: PlannedTerminal[];
  agents: AgentStatus[];
  accountsFor: (platform: string) => AgentAccount[];
  onPlanned: (
    update: (previous: PlannedTerminal[]) => PlannedTerminal[],
  ) => void;
  /**
   * Re-read the agent list, because the user just added, edited or removed a
   * CLI of their own.
   *
   * Passed in rather than done here: this component RENDERS the list it is
   * given, and the owner of that list is the one that can replace it. Without
   * it the new CLI would be stored, registered and offered everywhere — except
   * in the very screen the user added it from, until the next full refresh.
   */
  onAgentsChanged?: () => void;
}

/** Count assigned panes without treating the unassigned sentinel as an agent. */
export function allocationCounts(planned: PlannedTerminal[]): Counts {
  const counts: Counts = {};
  for (const pane of planned) {
    if (!pane.agent) continue;
    counts[pane.agent] = (counts[pane.agent] ?? 0) + 1;
  }
  return counts;
}

/**
 * Set one agent's count. If every slot is occupied, a larger count takes slots
 * from the largest other allocation first. Typing 7 beside Codex can therefore
 * turn an initial 17/0 split into 10/7 in one action.
 */
export function setAllocationCount(
  planned: PlannedTerminal[],
  agentOrder: string[],
  agent: string,
  requested: number,
): Counts {
  const total = planned.length;
  const counts = allocationCounts(planned);
  counts[agent] = Math.max(0, Math.min(total, Math.trunc(requested || 0)));

  let overflow =
    Object.values(counts).reduce((sum, value) => sum + value, 0) - total;
  if (overflow <= 0) return counts;

  const donors = agentOrder
    .filter((name) => name !== agent && (counts[name] ?? 0) > 0)
    .sort((a, b) => (counts[b] ?? 0) - (counts[a] ?? 0));
  for (const donor of donors) {
    const taken = Math.min(overflow, counts[donor] ?? 0);
    counts[donor] = (counts[donor] ?? 0) - taken;
    overflow -= taken;
    if (overflow === 0) break;
  }
  return counts;
}

/** Expand aggregate counts back into the backend's per-pane plan. */
export function applyAllocation(
  previous: PlannedTerminal[],
  agentOrder: string[],
  counts: Counts,
  preferredAccounts?: Record<string, string | undefined>,
): PlannedTerminal[] {
  const assignments: string[] = [];
  const known = new Set(agentOrder);
  const order = [
    ...agentOrder,
    ...Object.keys(counts)
      .filter((name) => !known.has(name))
      .sort(),
  ];
  for (const agent of order) {
    const count = Math.max(0, Math.trunc(counts[agent] ?? 0));
    for (
      let index = 0;
      index < count && assignments.length < previous.length;
      index += 1
    ) {
      assignments.push(agent);
    }
  }

  return previous.map((pane, index) => {
    const agent = assignments[index] ?? "";
    if (!agent) return { name: pane.name, agent: "" };
    return {
      name: pane.name,
      agent,
      account: preferredAccounts?.[agent],
    };
  });
}

function preferredAccounts(
  planned: PlannedTerminal[],
): Record<string, string | undefined> {
  const result: Record<string, string | undefined> = {};
  for (const pane of planned) {
    if (pane.agent && !(pane.agent in result))
      result[pane.agent] = pane.account;
  }
  return result;
}

export function AgentAllocation({
  planned,
  agents,
  accountsFor,
  onPlanned,
  onAgentsChanged,
}: AgentAllocationProps) {
  const t = useT();
  /*
   * The dialog needs the FULL stored record — the command, the logo, how a
   * dropped file is written — and `/agents` carries only what a picker shows.
   * Fetched on demand, when the user reaches for edit or add, rather than kept
   * in step with the list: on the common path (nobody touches their own CLIs)
   * that is a request this screen never makes.
   */
  const [editing, setEditing] = useState<CustomCli | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const openDialog = async (agent?: AgentStatus) => {
    setError("");
    if (!agent) {
      setEditing(null);
      setDialogOpen(true);
      return;
    }
    setBusy(agent.name);
    try {
      const stored = await fetchCustomClis();
      const found = stored.clis.find((cli) => cli.id === agent.name) ?? null;
      if (!found) throw new Error(t("custom_cli.gone"));
      setEditing(found);
      setDialogOpen(true);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  };

  const removeCli = async (agent: AgentStatus) => {
    setError("");
    setBusy(agent.name);
    try {
      await deleteCustomCli(agent.name);
      // Free the slots it held. A pane planned for a CLI that no longer exists
      // would be rejected by /launch, so the count goes back to unassigned
      // rather than leaving the user to notice at the end of the wizard.
      onPlanned((previous) =>
        previous.map((pane) =>
          pane.agent === agent.name ? { name: pane.name, agent: "" } : pane,
        ),
      );
      onAgentsChanged?.();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  };

  const counts = allocationCounts(planned);
  const assigned = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const total = planned.length;
  const unassigned = Math.max(0, total - assigned);
  const installed = agents.filter((agent) => agent.installed);
  const agentOrder = agents.map((agent) => agent.name);

  const commitCounts = (next: Counts) =>
    onPlanned((previous) =>
      applyAllocation(previous, agentOrder, next, preferredAccounts(previous)),
    );

  const setCount = (agent: string, next: number) =>
    onPlanned((previous) =>
      applyAllocation(
        previous,
        agentOrder,
        setAllocationCount(previous, agentOrder, agent, next),
        preferredAccounts(previous),
      ),
    );

  const oneEach = () => {
    const next: Counts = {};
    installed.slice(0, total).forEach((agent) => {
      next[agent.name] = 1;
    });
    commitCounts(next);
  };

  const splitEvenly = () => {
    const next: Counts = {};
    if (installed.length === 0) return commitCounts(next);
    const base = Math.floor(total / installed.length);
    const remainder = total % installed.length;
    installed.forEach((agent, index) => {
      next[agent.name] = base + (index < remainder ? 1 : 0);
    });
    commitCounts(next);
  };

  const setAccount = (agent: string, account: string | undefined) =>
    onPlanned((previous) =>
      previous.map((pane) =>
        pane.agent === agent ? { ...pane, account } : pane,
      ),
    );

  return (
    <div>
      <div className="border-b border-border/70 pb-5">
        <div className="flex items-end justify-between gap-5">
          <div>
            <SectionLabel>
              {t("workspace_launcher.agents.allocation")}
            </SectionLabel>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              {t("workspace_launcher.agents.hint")}
            </p>
          </div>
          <p className="shrink-0 font-mono text-sm tabular-nums text-foreground">
            {assigned} / {total}
          </p>
        </div>
        <div
          className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label={t("workspace_launcher.agents.assigned")}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={assigned}
        >
          <div
            className="h-full origin-left rounded-full bg-primary transition-transform duration-200"
            style={{
              transform: `scaleX(${total === 0 ? 0 : assigned / total})`,
            }}
          />
        </div>
        <p className="mt-2 text-right text-[11px] text-muted-foreground">
          {unassigned === 0
            ? t("workspace_launcher.agents.all_assigned")
            : t("workspace_launcher.agents.unassigned").replace(
                "{0}",
                String(unassigned),
              )}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-border/70 py-4">
        <SectionLabel className="mr-1">
          {t("workspace_launcher.agents.quick_fill")}
        </SectionLabel>
        <Button
          variant="quiet"
          onClick={oneEach}
          disabled={installed.length === 0}
        >
          {t("workspace_launcher.agents.one_each")}
        </Button>
        <Button
          variant="quiet"
          onClick={splitEvenly}
          disabled={installed.length === 0}
        >
          {t("workspace_launcher.agents.split_evenly")}
        </Button>
        <Button
          variant="subtle"
          onClick={() => commitCounts({})}
          disabled={assigned === 0}
        >
          {t("workspace_launcher.agents.clear")}
        </Button>
      </div>

      <div className="border-b border-border/70">
        {agents.map((agent) => {
          const count = counts[agent.name] ?? 0;
          const accounts = accountsFor(agent.name);
          const selectedAccount =
            planned.find((pane) => pane.agent === agent.name)?.account ?? "";
          return (
            <div
              key={agent.name}
              className={cn(
                "group grid gap-3 border-b border-border/50 px-1 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center",
                count > 0 && "bg-primary/[0.035]",
              )}
            >
              <div className="flex min-w-0 items-center gap-3">
                <button
                  type="button"
                  aria-pressed={count > 0}
                  aria-label={t("workspace_launcher.agents.select").replace(
                    "{0}",
                    agent.display_name,
                  )}
                  disabled={!agent.installed}
                  onClick={() => setCount(agent.name, count > 0 ? 0 : 1)}
                  className={cn(
                    "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-control transition-transform active:scale-95",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
                  )}
                >
                  <AgentMark
                    agent={agent.name}
                    label={agent.display_name}
                    logoUrl={agent.logo_url || undefined}
                    className={cn(
                      "transition-colors",
                      count > 0
                        ? "border-primary/70 bg-primary/[0.08]"
                        : "hover:border-primary/40",
                    )}
                  />
                  {count > 0 && (
                    <span className="absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full border-2 border-background bg-primary text-primary-foreground">
                      <Check className="h-2.5 w-2.5 stroke-[3]" />
                    </span>
                  )}
                </button>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <span className="font-medium text-foreground">
                      {agent.display_name}
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      {agent.installed
                        ? agent.version ||
                          t("workspace_launcher.agents.installed")
                        : t("workspace_launcher.agents.not_installed")}
                    </span>
                    {/* Editing lives on the row it belongs to, and only on the
                        rows it CAN belong to — a shipped CLI has nothing here
                        to change. Quiet until the row is hovered or a key
                        reaches them, so six built-in rows do not grow two
                        buttons each for a feature most of them cannot use. */}
                    {agent.custom && (
                      <span className="flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                        <IconButton
                          size="sm"
                          label={t("custom_cli.edit_named").replace(
                            "{0}",
                            agent.display_name,
                          )}
                          disabled={busy === agent.name}
                          data-testid={`custom-cli-edit-${agent.name}`}
                          onClick={() => void openDialog(agent)}
                        >
                          <Pencil className="h-3 w-3" aria-hidden="true" />
                        </IconButton>
                        <IconButton
                          size="sm"
                          label={t("custom_cli.remove_named").replace(
                            "{0}",
                            agent.display_name,
                          )}
                          disabled={busy === agent.name}
                          data-testid={`custom-cli-remove-${agent.name}`}
                          onClick={() => void removeCli(agent)}
                          className="hover:text-destructive"
                        >
                          <Trash2 className="h-3 w-3" aria-hidden="true" />
                        </IconButton>
                      </span>
                    )}
                  </div>
                  {agent.description && (
                    <p className="truncate text-[11px] text-muted-foreground">
                      {agent.description}
                    </p>
                  )}
                  {accounts.length >= 2 && count > 0 && (
                    <label className="mt-2 flex max-w-sm items-center gap-2 text-[11px] text-muted-foreground">
                      <span>{t("workspace_launcher.agents.account")}</span>
                      <BrandedSelect
                        value={selectedAccount}
                        ariaLabel={t(
                          "workspace_launcher.agents.account_for",
                        ).replace("{0}", agent.display_name)}
                        onValueChange={(value) =>
                          setAccount(agent.name, value || undefined)
                        }
                        className="h-7 min-w-0 flex-1 text-xs"
                        options={[
                          {
                            value: "",
                            label: t(
                              "workspace_launcher.agents.active_account",
                            ),
                          },
                          ...accounts.map((account) => ({
                            value: account.id,
                            label: `${account.label}${
                              account.connected
                                ? ""
                                : ` (${t("workspace_launcher.agents.not_signed_in")})`
                            }`,
                          })),
                        ]}
                      />
                    </label>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="subtle"
                  className="h-10 min-w-12 px-3 text-[10px] uppercase tracking-[0.12em]"
                  disabled={!agent.installed}
                  onClick={() => commitCounts({ [agent.name]: total })}
                >
                  {t("workspace_launcher.agents.all")}
                </Button>
                <div
                  data-testid={`allocation-stepper-${agent.name}`}
                  className="grid grid-cols-[2.5rem_6.5rem_2.5rem] items-center overflow-hidden rounded-control border border-border bg-background shadow-inner transition-colors focus-within:border-primary/60 focus-within:ring-1 focus-within:ring-primary/20"
                >
                  <button
                    type="button"
                    aria-label={t("workspace_launcher.agents.decrease").replace(
                      "{0}",
                      agent.display_name,
                    )}
                    disabled={!agent.installed || count === 0}
                    onClick={() => setCount(agent.name, count - 1)}
                    className="flex h-10 items-center justify-center text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground active:scale-95 disabled:opacity-30"
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <label className="flex h-10 min-w-0 items-center justify-center border-x border-border bg-card/30 px-2 focus-within:bg-secondary/45">
                    <input
                      type="number"
                      inputMode="numeric"
                      step={1}
                      min={0}
                      max={total}
                      value={count}
                      aria-label={t(
                        "workspace_launcher.agents.count_for",
                      ).replace("{0}", agent.display_name)}
                      disabled={!agent.installed}
                      onFocus={(event) => event.currentTarget.select()}
                      onChange={(event) =>
                        setCount(
                          agent.name,
                          Number.isFinite(event.currentTarget.valueAsNumber)
                            ? event.currentTarget.valueAsNumber
                            : 0,
                        )
                      }
                      className="h-10 w-10 border-0 bg-transparent p-0 text-right font-mono text-base font-semibold tabular-nums text-foreground outline-none [appearance:textfield] disabled:opacity-40 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                    />
                    <span
                      aria-hidden="true"
                      className="ml-1 whitespace-nowrap font-mono text-[10px] tabular-nums text-muted-foreground"
                    >
                      / {total}
                    </span>
                  </label>
                  <button
                    type="button"
                    aria-label={t("workspace_launcher.agents.increase").replace(
                      "{0}",
                      agent.display_name,
                    )}
                    disabled={!agent.installed || count >= total}
                    onClick={() => setCount(agent.name, count + 1)}
                    className="flex h-10 items-center justify-center text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground active:scale-95 disabled:opacity-30"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Adding your own sits at the END of the list rather than in a settings
          page, because this is where the question comes up: the user is
          looking at six CLIs and the one they wanted is not among them. */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-1 py-3">
        <div className="min-w-0">
          <p className="text-sm text-foreground">{t("custom_cli.add_title")}</p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("custom_cli.add_hint")}
          </p>
        </div>
        <Button
          variant="quiet"
          data-testid="custom-cli-add"
          onClick={() => void openDialog()}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          {t("custom_cli.add_button")}
        </Button>
      </div>

      {error && (
        <p
          role="alert"
          data-testid="custom-cli-list-error"
          className="mt-3 border-l-2 border-destructive/70 py-1 pl-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        {t("workspace_launcher.agents.auto_names")}
      </p>

      <CustomCliDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editing={editing}
        onSaved={() => onAgentsChanged?.()}
      />
    </div>
  );
}
