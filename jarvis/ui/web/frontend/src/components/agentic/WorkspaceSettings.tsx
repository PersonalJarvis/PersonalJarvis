/**
 * The workspace's own settings, opened from the Agentic-IDE toolbar.
 *
 * One subject so far, and it is the one that has to be reachable from INSIDE
 * the workspace rather than three sections away in the app's settings: which
 * subscription the next terminal opens on. Someone running two Claude Max seats
 * decides that while looking at the panes — "the next one goes on the personal
 * plan" — and sending them out of the workspace to say so means losing the
 * screen they were reasoning about.
 *
 * Three things this surface is careful about:
 *
 * 1. **It says where the switch lands.** Switching applies to terminals opened
 *    from now on; every pane already on screen keeps the account it started
 *    with, because moving a running agent onto a plan whose history has never
 *    seen its conversation would make it amnesiac with no explanation.
 * 2. **It stays quiet when there is nothing to choose.** The active-account
 *    chips appear only once a second subscription of that CLI is registered.
 *    The gear itself is always there — it is also where the second one is added.
 * 3. **It is the SAME panel as the app's account page**, not a second copy of
 *    it. Only how the switch is applied differs: here it goes through the
 *    workspace, so the open session learns about it in the same round-trip.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Settings2, Users, X } from "lucide-react";

import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import type { AccountPlatform } from "@/lib/agentAccountsApi";
import {
  setIdeActiveAccount,
  type IdeAccountState,
  type IdeState,
} from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

interface WorkspaceSettingsProps {
  /** The active subscription per coding CLI, as the backend reports it. */
  accounts: IdeAccountState[];
  /** A switch changed the workspace state — the owner applies what came back. */
  onStateChanged?: (state: IdeState) => void;
  /** True while the workspace is busy with something that must finish first. */
  busy?: boolean;
}

/** Coding CLIs where more than one subscription is registered — the rest are silent. */
function switchable(accounts: IdeAccountState[]): IdeAccountState[] {
  return accounts.filter((a) => a.account_count > 1 && Boolean(a.active_label));
}

export function WorkspaceSettings({
  accounts,
  onStateChanged,
  busy = false,
}: WorkspaceSettingsProps) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const chips = switchable(accounts);

  /*
   * Applying the switch through the WORKSPACE rather than through the account
   * store directly. Both end up writing the same stored default — but this way
   * the open session is told in the same call, so the account the next pane
   * gets and the account the toolbar shows can never be one click apart.
   *
   * Returns nothing on purpose: the panel then re-reads the account list, which
   * is the shorter path to a list that is right about sign-in state too.
   */
  const activate = async (platform: AccountPlatform, accountId: string) => {
    const state = await setIdeActiveAccount(platform, accountId);
    onStateChanged?.(state);
    const account = state.accounts?.find((a) => a.agent === platform);
    if (account?.active_label) {
      pushToast(
        "success",
        `New ${account.display_name} terminals will use ${account.active_label}.`,
      );
    }
  };

  return (
    <div className="flex shrink-0 items-center gap-2">
      {/* The chip looks like a button, so it must BE one: it names the plan the
          next terminal spends, and clicking it opens the switcher. Its first
          life as a passive <span> read as "the switch is broken" — the one
          interactive pixel was the gear beside it. */}
      {chips.map((account) => (
        <button
          key={account.agent}
          type="button"
          data-testid={`active-account-${account.agent}`}
          onClick={() => setOpen(true)}
          title={`New ${account.display_name} terminals open on ${account.active_label}. Panes already running keep theirs. Click to switch.`}
          aria-label={`Switch which ${account.display_name} subscription new terminals use`}
          className={cn(
            "hidden h-7 max-w-[16rem] items-center gap-1.5 rounded-md bg-secondary/50 px-2 text-[11px] text-muted-foreground transition-colors xl:flex",
            "hover:bg-secondary hover:text-foreground",
          )}
        >
          <Users className="h-3 w-3 shrink-0 text-primary" />
          <span className="truncate">{account.active_label}</span>
        </button>
      ))}

      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Trigger asChild>
          <button
            type="button"
            data-testid="agentic-settings-open"
            title="Workspace settings — choose which subscription new terminals use"
            aria-label="Workspace settings"
            className={cn(
              "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors",
              "hover:bg-secondary hover:text-foreground",
            )}
          >
            <Settings2 className="h-4 w-4" />
          </button>
        </Dialog.Trigger>

        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-background/70 backdrop-blur-sm" />
          <Dialog.Content
            data-testid="agentic-settings"
            className="fixed left-1/2 top-1/2 z-50 flex max-h-[min(44rem,calc(100vh-4rem))] w-[min(52rem,calc(100vw-3rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-2xl border border-border bg-card shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="font-display text-base font-semibold">
                  Workspace settings
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-xs text-muted-foreground">
                  Connect several subscriptions of the same coding CLI and choose
                  which one powers new agent terminals.
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label="Close settings"
                  className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </Dialog.Close>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-5 py-4">
              {/* What is in force right now, in words rather than ids — the
                  answer to "which plan is the next terminal going to spend?". */}
              <ul className="mb-4 grid gap-2 sm:grid-cols-2">
                {accounts.map((account) => (
                  <li
                    key={account.agent}
                    className="rounded-xl border border-border bg-background/40 px-3 py-2"
                  >
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {account.display_name} · new terminals
                    </p>
                    <p
                      className="truncate text-sm font-medium"
                      data-testid={`settings-active-${account.agent}`}
                    >
                      {account.active_label ?? "—"}
                    </p>
                  </li>
                ))}
              </ul>

              <AgentAccountsPanel
                onActivate={activate}
                note={
                  "Switching applies to every terminal opened from now on, splits " +
                  "included — the panes you already have keep the account they " +
                  "started with, so no running agent loses its conversation. Only " +
                  "a pane whose subscription was chosen deliberately (in the " +
                  "workspace wizard) passes that choice on when you split it."
                }
              />
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
              <Dialog.Close asChild>
                <button type="button" className="btn-ghost" disabled={busy}>
                  Done
                </button>
              </Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

export default WorkspaceSettings;
