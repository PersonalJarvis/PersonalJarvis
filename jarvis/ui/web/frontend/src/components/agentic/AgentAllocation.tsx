/**
 * Allocate terminal slots by agent instead of editing every pane separately.
 * The backend still receives its existing one-entry-per-terminal plan.
 */
import { Check, Minus, Plus, SquareTerminal } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { AgentAccount } from "@/lib/agentAccountsApi";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import { Button, Select, SectionLabel } from "./controls";

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
}: AgentAllocationProps) {
  const t = useT();
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
                "grid gap-3 border-b border-border/50 px-1 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center",
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
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-control border transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
                    count > 0
                      ? "border-primary/60 bg-primary text-primary-foreground"
                      : "border-border bg-card/60 text-muted-foreground hover:border-primary/40",
                  )}
                >
                  {count > 0 ? (
                    <Check className="h-4 w-4" />
                  ) : (
                    <SquareTerminal className="h-4 w-4" />
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
                  </div>
                  {agent.description && (
                    <p className="truncate text-[11px] text-muted-foreground">
                      {agent.description}
                    </p>
                  )}
                  {accounts.length >= 2 && count > 0 && (
                    <label className="mt-2 flex max-w-sm items-center gap-2 text-[11px] text-muted-foreground">
                      <span>{t("workspace_launcher.agents.account")}</span>
                      <Select
                        value={selectedAccount}
                        aria-label={t(
                          "workspace_launcher.agents.account_for",
                        ).replace("{0}", agent.display_name)}
                        onChange={(event) =>
                          setAccount(
                            agent.name,
                            event.target.value || undefined,
                          )
                        }
                        className="h-7 min-w-0 flex-1 text-xs"
                      >
                        <option value="">
                          {t("workspace_launcher.agents.active_account")}
                        </option>
                        {accounts.map((account) => (
                          <option key={account.id} value={account.id}>
                            {account.label}
                            {account.connected
                              ? ""
                              : ` (${t("workspace_launcher.agents.not_signed_in")})`}
                          </option>
                        ))}
                      </Select>
                    </label>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-end gap-1.5">
                <Button
                  variant="subtle"
                  className="px-2 text-xs uppercase tracking-wide"
                  disabled={!agent.installed}
                  onClick={() => commitCounts({ [agent.name]: total })}
                >
                  {t("workspace_launcher.agents.all")}
                </Button>
                <div className="grid grid-cols-[2rem_3rem_2rem] items-center rounded-control border border-border bg-background">
                  <button
                    type="button"
                    aria-label={t("workspace_launcher.agents.decrease").replace(
                      "{0}",
                      agent.display_name,
                    )}
                    disabled={!agent.installed || count === 0}
                    onClick={() => setCount(agent.name, count - 1)}
                    className="flex h-8 items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-30"
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <input
                    type="number"
                    min={0}
                    max={total}
                    value={count}
                    aria-label={t(
                      "workspace_launcher.agents.count_for",
                    ).replace("{0}", agent.display_name)}
                    disabled={!agent.installed}
                    onChange={(event) =>
                      setCount(agent.name, Number(event.target.value))
                    }
                    className="h-8 min-w-0 border-x border-border bg-transparent text-center font-mono text-sm tabular-nums text-foreground outline-none focus:bg-secondary/50 disabled:opacity-40"
                  />
                  <button
                    type="button"
                    aria-label={t("workspace_launcher.agents.increase").replace(
                      "{0}",
                      agent.display_name,
                    )}
                    disabled={!agent.installed || count >= total}
                    onClick={() => setCount(agent.name, count + 1)}
                    className="flex h-8 items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-30"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        {t("workspace_launcher.agents.auto_names")}
      </p>
    </div>
  );
}
