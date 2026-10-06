import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronRight, Loader2, LogIn, Pencil, Plus, Trash2 } from "lucide-react";
import { AccountUsageMeters } from "@/components/AccountUsageMeters";
import { LoginFlowBox } from "@/components/AgentAccountsPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import {
  cancelLoginFlow,
  createAgentAccount,
  deleteAgentAccount,
  fetchAgentAccounts,
  fetchAgentUsage,
  getLoginFlow,
  groupFor,
  loginAgentAccount,
  renameAgentAccount,
  setActiveAgentAccount,
  startLoginFlow,
  type AccountUsage,
  type AgentAccount,
  type AgentAccountsResponse,
  type LoginFlowState,
} from "@/lib/agentAccountsApi";
import type { SubscriptionKind } from "@/lib/providerFamilies";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { SettingsGroup } from "./settingsUi";

/**
 * Which account registry holds a CLI's subscriptions. A CLI without an entry
 * (Antigravity) has one login only, and the section stays away.
 */
const ACCOUNT_PLATFORM: Partial<Record<SubscriptionKind, string>> = {
  claude_cli: "claude",
  codex: "codex",
  grok_build: "grok-build",
};

/** The highest share of any plan limit — the one that stops the seat first. */
function usedPercent(usage: AccountUsage | undefined): { percent: number; critical: boolean } | null {
  if (!usage || usage.status !== "ok" || usage.windows.length === 0) return null;
  const top = usage.windows.reduce((a, b) => (b.percent > a.percent ? b : a));
  return { percent: Math.round(top.percent), critical: top.severity === "critical" || top.percent >= 100 };
}

/**
 * Every subscription connected for one CLI, and the way to add another.
 *
 * One row per seat: who it is, what plan, how much of it is spent, and which
 * one new terminals use. A row opens for its usage limits and the rarer
 * actions (switch, sign in, rename, remove); adding a seat names it and goes
 * straight into the in-app sign-in.
 */
export function SubscriptionAccounts({ kind }: { kind: SubscriptionKind }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const platform = ACCOUNT_PLATFORM[kind];
  const [data, setData] = useState<AgentAccountsResponse | null>(null);
  const [usage, setUsage] = useState<Record<string, AccountUsage>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState("");
  const [now, setNow] = useState(() => Date.now());
  // A seat added a moment ago starts its sign-in as soon as it is listed.
  const [signInFor, setSignInFor] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(await fetchAgentAccounts());
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    }
  }, [pushToast]);

  const loadUsage = useCallback(async () => {
    try {
      const body = await fetchAgentUsage();
      if (!body) return;
      setUsage(Object.fromEntries(body.accounts.map((entry) => [entry.account_id, entry])));
    } catch (cause) {
      // Usage is a hint next to each seat; without it the list still works.
      console.debug("account usage unavailable", cause);
    }
  }, []);

  useEffect(() => {
    if (!platform) return;
    void reload();
    void loadUsage();
    const usageTimer = window.setInterval(() => void loadUsage(), 60_000);
    const clock = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      window.clearInterval(usageTimer);
      window.clearInterval(clock);
    };
  }, [platform, reload, loadUsage]);

  if (!platform) return null;
  const group = groupFor(data, platform);
  const accounts = group?.accounts ?? [];

  async function run(key: string, action: () => Promise<AgentAccountsResponse | void>) {
    setBusy(key);
    try {
      const next = await action();
      if (next) setData(next);
      else await reload();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function add() {
    const name = label.trim();
    if (!name || !platform) return;
    const before = new Set(accounts.map((account) => account.id));
    setBusy("add");
    try {
      const next = await createAgentAccount(platform, name);
      setData(next);
      const created = groupFor(next, platform)?.accounts.find((account) => !before.has(account.id));
      setLabel("");
      setAdding(false);
      if (created) {
        setOpenId(created.id);
        setSignInFor(created.id);
      }
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-2.5" data-testid={`subscription-accounts-${kind}`}>
      <div className="flex min-h-7 items-center justify-between gap-4 px-1">
        <h2 className="flex items-center gap-2 text-sm font-normal text-foreground/70">
          {t("providers_page.accounts_section")}
          {accounts.length > 0 ? <span className="text-xs text-muted-foreground">{accounts.length}</span> : null}
        </h2>
        {!adding ? (
          <Button size="sm" variant="ghost" className="h-7 text-xs" data-testid="subscription-account-add" onClick={() => setAdding(true)}>
            <Plus />
            {t("agent_accounts.add")}
          </Button>
        ) : null}
      </div>
      <SettingsGroup>
        {accounts.map((account) => (
          <AccountLine
            key={account.id}
            account={account}
            active={account.id === group?.active_account}
            usage={usage[account.id]}
            now={now}
            open={openId === account.id}
            onToggle={() => setOpenId((id) => (id === account.id ? null : account.id))}
            busy={busy}
            run={run}
            autoSignIn={signInFor === account.id}
            onAutoSignInStarted={() => setSignInFor(null)}
          />
        ))}
        {accounts.length === 0 ? (
          <div className="px-4 py-3 text-xs text-muted-foreground">{t("agent_accounts.loading")}</div>
        ) : null}
        {adding ? (
          <div className="flex items-center gap-2 px-4 py-3">
            <Input
              autoFocus
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void add();
                if (event.key === "Escape") setAdding(false);
              }}
              placeholder={t("agent_accounts.name_placeholder")}
              aria-label={t("agent_accounts.name_placeholder")}
              className="h-8 min-w-0 flex-1 text-sm"
            />
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
              {t("agent_accounts.cancel")}
            </Button>
            <Button size="sm" disabled={!label.trim() || busy === "add"} onClick={() => void add()}>
              {busy === "add" && <Loader2 className="animate-spin" />}
              {t("agent_accounts.add_confirm")}
            </Button>
          </div>
        ) : null}
      </SettingsGroup>
    </section>
  );
}

function AccountLine({
  account,
  active,
  usage,
  now,
  open,
  onToggle,
  busy,
  run,
  autoSignIn,
  onAutoSignInStarted,
}: {
  account: AgentAccount;
  active: boolean;
  usage: AccountUsage | undefined;
  now: number;
  open: boolean;
  onToggle: () => void;
  busy: string | null;
  run: (key: string, action: () => Promise<AgentAccountsResponse | void>) => Promise<void>;
  autoSignIn: boolean;
  onAutoSignInStarted: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [flow, setFlow] = useState<LoginFlowState | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(account.label);
  const pending = busy?.endsWith(account.id) ?? false;
  const used = usedPercent(usage);
  const plan = usage?.plan ?? account.tier;

  const signIn = useCallback(async () => {
    try {
      setFlow(await startLoginFlow(account.id));
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    }
  }, [account.id, pushToast]);

  useEffect(() => {
    if (!autoSignIn) return;
    onAutoSignInStarted();
    void signIn();
  }, [autoSignIn, onAutoSignInStarted, signIn]);

  // Poll a running sign-in until it finishes; success re-reads the list.
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    if (!flow || flow.finished) return;
    const flowId = flow.flow_id;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const next = await getLoginFlow(flowId);
          if (!next.finished) {
            setFlow(next);
          } else if (next.status === "success") {
            pushToast("success", `${account.label}: ${t("agent_accounts.flow.success")}`);
            setFlow(null);
            await runRef.current(`flow:${account.id}`, async () => {});
          } else {
            setFlow(next);
          }
        } catch {
          // The flow is gone (the server restarted); stop polling it.
          setFlow(null);
        }
      })();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [flow, account.id, account.label, pushToast, t]);

  async function rename() {
    const name = draft.trim();
    setRenaming(false);
    if (!name || name === account.label) return;
    await run(`rename:${account.id}`, () => renameAgentAccount(account.id, name));
  }

  return (
    <div data-testid={`subscription-account-row-${account.id}`}>
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-90")}
        />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className={cn("truncate text-sm font-medium", account.connected ? "text-foreground" : "text-muted-foreground")}>
              {account.email || account.label}
            </span>
            {active ? (
              <span className="shrink-0 rounded border border-border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("providers_page.account_active")}
              </span>
            ) : null}
            {account.warning ? (
              <span title={account.warning} className="inline-flex shrink-0">
                <AlertTriangle aria-label={account.warning} className="h-3.5 w-3.5 text-warning" />
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {[account.label, plan].filter(Boolean).join(" · ")}
            {!account.connected ? ` · ${t("agent_accounts.not_signed_in")}` : ""}
          </span>
        </span>
        {used ? (
          <span className={cn("shrink-0 text-sm tabular-nums", used.critical ? "text-destructive" : "text-muted-foreground")}>
            {used.percent}%
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="space-y-3 px-4 pb-4 pl-11">
          {account.warning ? <p className="text-xs text-warning">{account.warning}</p> : null}
          <AccountUsageMeters usage={usage} now={now} />
          <div className="flex flex-wrap items-center gap-2">
            {!active && account.connected ? (
              <Button
                size="sm"
                variant="outline"
                disabled={pending}
                onClick={() => void run(`active:${account.id}`, () => setActiveAgentAccount(account.platform, account.id))}
              >
                {t("providers_page.account_use")}
              </Button>
            ) : null}
            {!account.connected && !flow ? (
              <Button size="sm" disabled={pending} onClick={() => void signIn()}>
                <LogIn />
                {t("agent_accounts.sign_in")}
              </Button>
            ) : null}
            {!account.builtin ? (
              renaming ? (
                <Input
                  autoFocus
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={() => void rename()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void rename();
                    if (event.key === "Escape") setRenaming(false);
                  }}
                  aria-label={t("agent_accounts.rename")}
                  className="h-8 w-48 text-sm"
                />
              ) : (
                <>
                  <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => { setDraft(account.label); setRenaming(true); }}>
                    <Pencil />
                    {t("agent_accounts.rename")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground hover:text-destructive"
                    disabled={pending}
                    onClick={() => void run(`delete:${account.id}`, () => deleteAgentAccount(account.id))}
                  >
                    <Trash2 />
                    {t("agent_accounts.remove")}
                  </Button>
                </>
              )
            ) : null}
          </div>
          {flow ? (
            <LoginFlowBox
              flow={flow}
              onFlow={setFlow}
              onClose={() => {
                // Closing only abandons the sign-in; a cancel that fails leaves a
                // CLI process the server reaps on its own timeout.
                if (!flow.finished) void cancelLoginFlow(flow.flow_id).catch(() => undefined);
                setFlow(null);
              }}
              onRetry={() => void signIn()}
              onFallback={() => {
                setFlow(null);
                void run(`login:${account.id}`, async () => {
                  const { message } = await loginAgentAccount(account.id);
                  pushToast("info", message);
                });
              }}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
