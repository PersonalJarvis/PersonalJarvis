/**
 * Several subscriptions per coding CLI, switchable without signing out.
 *
 * Someone holding two Claude Max seats (or two ChatGPT/Codex plans) could only
 * reach one of them: each CLI keeps a single login, so getting to the other
 * meant a logout that threw the first away. Here both are registered side by
 * side and the switch is a click — because an account is nothing but a config
 * directory, and switching decides which directory the next terminal opens
 * against.
 *
 * Two things this panel is careful about, both of which would otherwise mislead:
 *
 * 1. **"Active" means new terminals, not running ones.** A pane keeps the
 *    account it was opened with, so switching never moves an agent
 *    mid-conversation onto a plan whose history has never seen it. The
 *    subtitle says so rather than leaving the user to find out.
 * 2. **A registered account is not a signed-in one.** Adding mints a folder;
 *    the sign-in is a separate, deliberate step. An account with no login says
 *    exactly that instead of showing a hopeful green row.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ClipboardPaste,
  Copy,
  ExternalLink,
  KeyRound,
  Loader2,
  LogIn,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Users,
  X,
} from "lucide-react";

import { useT } from "@/i18n";
import {
  type AccountPlatform,
  type AccountUsage,
  type AgentAccount,
  type AgentAccountsResponse,
  type LoginFlowState,
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
  submitLoginFlowCode,
} from "@/lib/agentAccountsApi";
import { AccountUsageMeters } from "./AccountUsageMeters";
import { robustCopy, robustPaste } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

/** How the switch is applied — see `onActivate`. */
type Activate = (
  platform: AccountPlatform,
  accountId: string,
) => Promise<AgentAccountsResponse | void>;

interface AgentAccountsPanelProps {
  /**
   * Applies the switch. Defaults to the stored-default route, which is what the
   * Settings page means by it.
   *
   * The Agentic IDE passes its own so the open workspace learns about the
   * change in the same round-trip — one panel, one behaviour, two places it can
   * be reached from. Anything it returns replaces the list; returning nothing
   * makes the panel re-read it.
   */
  onActivate?: Activate;
  /** One extra line under the description, e.g. where the switch takes effect. */
  note?: string;
}

export function AgentAccountsPanel({ onActivate, note }: AgentAccountsPanelProps = {}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [data, setData] = useState<AgentAccountsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [usage, setUsage] = useState<Record<string, AccountUsage>>({});
  const [usageTtl, setUsageTtl] = useState(60);
  const [refreshing, setRefreshing] = useState(false);
  // Assumed true until a 404 proves otherwise, so the meters appear on the
  // first successful read instead of after a round trip spent proving support.
  const [usageAvailable, setUsageAvailable] = useState(true);
  // One clock for every countdown on screen. Per-meter timers would drift
  // against each other and a card with four seats would run a dozen intervals
  // to render the same minute.
  const [now, setNow] = useState(() => Date.now());

  const reload = useCallback(async () => {
    try {
      setData(await fetchAgentAccounts());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /**
   * Usage is fetched SEPARATELY from the account list, and that separation is
   * the point: the list is a handful of local file reads and must stay instant,
   * while a reading costs one network round trip per seat. Folding them into
   * one request would make opening this panel as slow as the slowest provider.
   */
  const loadUsage = useCallback(async (force = false) => {
    try {
      const body = await fetchAgentUsage(force);
      if (body === null) {
        // This backend predates the usage route — the app's server does not
        // pick up new routes while it is running, so this is the ordinary state
        // between an update and the next restart. Hide the block entirely
        // rather than offering a control that cannot work.
        setUsageAvailable(false);
        setUsage({});
        return;
      }
      setUsageAvailable(true);
      const next: Record<string, AccountUsage> = {};
      for (const entry of body.accounts ?? []) next[entry.account_id] = entry;
      setUsage(next);
      if (body.ttl_seconds > 0) setUsageTtl(body.ttl_seconds);
      setNow(Date.now());
    } catch {
      // Usage sits on top of the switcher, so a failed read keeps the previous
      // numbers (each already labelled with its own age) and never costs the
      // account list — which is the part the user came here to operate.
    }
  }, []);

  // Re-read whenever the set of accounts changes, which is what makes a fresh
  // sign-in show its plan straight away instead of after the next interval.
  const accountKey = (data?.platforms ?? [])
    .flatMap((group) => group.accounts.map((account) => account.id))
    .join(",");
  useEffect(() => {
    if (accountKey) void loadUsage();
  }, [accountKey, loadUsage]);

  // Poll on the server's own cache lifetime rather than on a second number
  // kept in step by hand. Floored so a short server TTL cannot turn this into
  // a request loop.
  useEffect(() => {
    // Polling a route that answered 404 would be a request a minute, forever,
    // for an answer that cannot change without a server restart.
    if (!usageAvailable) return;
    const period = Math.max(30, usageTtl) * 1000;
    const timer = setInterval(() => void loadUsage(), period);
    return () => clearInterval(timer);
  }, [loadUsage, usageTtl, usageAvailable]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const refreshUsage = useCallback(async () => {
    setRefreshing(true);
    try {
      await loadUsage(true);
    } finally {
      setRefreshing(false);
    }
  }, [loadUsage]);

  /** Run one account action, keeping the list and any error message honest. */
  const run = useCallback(
    async (key: string, action: () => Promise<AgentAccountsResponse | void>) => {
      setBusy(key);
      try {
        const next = await action();
        if (next) setData(next);
        else await reload();
        setError(null);
      } catch (e) {
        pushToast("error", (e as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [pushToast, reload],
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Users className="h-4 w-4 text-violet-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-violet-400">
          {t("agent_accounts.title")}
        </span>
        <span className="text-[11px] text-muted-foreground">
          · {t("agent_accounts.hint")}
        </span>
        {usageAvailable && (
          <button
            type="button"
            onClick={() => void refreshUsage()}
            disabled={refreshing}
            aria-label={t("agent_accounts.usage.refresh")}
            title={t("agent_accounts.usage.refresh")}
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-lg border border-border px-2 py-1 text-[10px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />
            {t("agent_accounts.usage.refresh")}
          </button>
        )}
      </div>
      <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
        {t("agent_accounts.description")}
      </p>
      {note && (
        <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
          {note}
        </p>
      )}

      {error && (
        <p className="px-1 text-[11px] text-amber-600" role="alert">
          {error}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2 md:items-start">
        {/*
          * Rendered from what the API returned, never from a list kept here.
          * The backend decides which CLIs can hold several subscriptions, and a
          * hardcoded list fails in the one way nobody catches: the payload is
          * right, the card is simply absent, and the feature looks unbuilt.
          */}
        {(data?.platforms ?? []).map((group) => (
          <PlatformCard
            key={group.platform}
            platform={group.platform}
            group={group}
            busy={busy}
            run={run}
            activate={onActivate ?? setActiveAgentAccount}
            usage={usage}
            now={now}
          />
        ))}
      </div>
    </div>
  );
}

function PlatformCard({
  platform,
  group,
  busy,
  run,
  activate,
  usage,
  now,
}: {
  platform: AccountPlatform;
  group: ReturnType<typeof groupFor>;
  busy: string | null;
  run: (key: string, action: () => Promise<AgentAccountsResponse | void>) => Promise<void>;
  activate: Activate;
  usage: Record<string, AccountUsage>;
  now: number;
}) {
  const t = useT();
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState("");

  const accounts = group?.accounts ?? [];
  const activeId = group?.active_account ?? null;

  async function add() {
    const name = label.trim();
    if (!name) return;
    await run(`add:${platform}`, () => createAgentAccount(platform, name));
    setLabel("");
    setAdding(false);
  }

  return (
    <div className="relative flex flex-col gap-3 rounded-2xl border border-border bg-card/60 p-4 backdrop-blur">
      <span
        aria-hidden="true"
        className="absolute bottom-4 left-0 top-4 w-[3px] rounded-r-full bg-violet-400/60"
      />
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{group?.display_name || platform}</h4>
        <span className="text-[11px] text-muted-foreground">
          {accounts.length} {t("agent_accounts.accounts")}
        </span>
      </div>

      <ul className="space-y-1.5">
        {accounts.map((account) => (
          <AccountRow
            key={account.id}
            account={account}
            active={account.id === activeId}
            busy={busy}
            run={run}
            activate={activate}
            usage={usage[account.id]}
            now={now}
          />
        ))}
        {accounts.length === 0 && (
          <li className="text-[11px] text-muted-foreground">
            {t("agent_accounts.loading")}
          </li>
        )}
      </ul>

      {adding ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void add();
              if (e.key === "Escape") setAdding(false);
            }}
            placeholder={t("agent_accounts.name_placeholder")}
            aria-label={t("agent_accounts.name_placeholder")}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            onClick={() => void add()}
            disabled={!label.trim() || busy === `add:${platform}`}
            className="shrink-0 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
          >
            {t("agent_accounts.add_confirm")}
          </button>
          <button
            type="button"
            onClick={() => setAdding(false)}
            className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs"
          >
            {t("agent_accounts.cancel")}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:border-primary/40"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("agent_accounts.add")}
        </button>
      )}
    </div>
  );
}

function AccountRow({
  account,
  active,
  busy,
  run,
  activate,
  usage,
  now,
}: {
  account: AgentAccount;
  active: boolean;
  busy: string | null;
  run: (key: string, action: () => Promise<AgentAccountsResponse | void>) => Promise<void>;
  activate: Activate;
  usage: AccountUsage | undefined;
  now: number;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(account.label);
  const [flow, setFlow] = useState<LoginFlowState | null>(null);
  const pending = busy?.endsWith(account.id) ?? false;

  async function use() {
    if (active) return;
    await run(`active:${account.id}`, () => activate(account.platform, account.id));
  }

  /**
   * Sign-in runs IN-APP: the CLI's own login on a hidden PTY, surfaced as a
   * copyable link and a code field. The external terminal window survives only
   * as the stated fallback — pasting the OAuth code into a raw console is
   * exactly where this flow kept dying (frozen paste, burned single-use code).
   */
  async function signIn() {
    try {
      setFlow(await startLoginFlow(account.id));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  }

  async function signInExternally() {
    setFlow(null);
    await run(`login:${account.id}`, async () => {
      const { message } = await loginAgentAccount(account.id);
      pushToast("info", message);
    });
  }

  const flowDone = useCallback(
    async (finished: LoginFlowState) => {
      if (finished.status === "success") {
        pushToast("success", `${account.label}: ${t("agent_accounts.flow.success")}`);
        setFlow(null);
        // An empty action makes `run` re-read the list, which is what flips
        // the row to its honest green state.
        await run(`flow:${account.id}`, async () => {});
      } else {
        setFlow(finished);
      }
    },
    [account.id, account.label, pushToast, run, t],
  );

  // Poll the running flow. The interval is re-armed per state snapshot, which
  // is cheap at this cadence and keeps the callback free of stale closures.
  const flowDoneRef = useRef(flowDone);
  flowDoneRef.current = flowDone;
  useEffect(() => {
    if (!flow || flow.finished) return;
    const flowId = flow.flow_id;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const next = await getLoginFlow(flowId);
          if (next.finished) await flowDoneRef.current(next);
          else setFlow(next);
        } catch {
          // The flow evaporated (server restart) — the box would poll forever.
          setFlow(null);
        }
      })();
    }, 1200);
    return () => clearInterval(timer);
  }, [flow]);

  async function rename() {
    const name = draft.trim();
    if (!name || name === account.label) {
      setRenaming(false);
      return;
    }
    await run(`rename:${account.id}`, () => renameAgentAccount(account.id, name));
    setRenaming(false);
  }

  return (
    <li
      className={cn(
        "flex flex-col gap-1.5 rounded-xl border px-3 py-2 transition-colors",
        active
          ? "border-primary/55 bg-primary/[0.06]"
          : "border-border/70 hover:border-primary/30",
      )}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void use()}
          disabled={active || pending}
          aria-pressed={active}
          aria-label={`${t("agent_accounts.use_account")}: ${account.label}`}
          title={t("agent_accounts.use_account")}
          className={cn(
            "grid h-4 w-4 shrink-0 place-items-center rounded-full border",
            active
              ? "border-primary bg-primary text-primary-foreground"
              : "border-muted-foreground/50 hover:border-primary",
          )}
        >
          {active && <Check className="h-2.5 w-2.5" />}
        </button>

        {renaming ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void rename();
              if (e.key === "Escape") setRenaming(false);
            }}
            onBlur={() => void rename()}
            aria-label={t("agent_accounts.rename")}
            className="min-w-0 flex-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-xs"
          />
        ) : (
          <span className="min-w-0 flex-1 truncate text-xs font-medium">
            {account.label}
          </span>
        )}

        {active && (
          <span className="chip-yellow shrink-0">{t("agent_accounts.in_use")}</span>
        )}

        {!account.connected && !flow && (
          <button
            type="button"
            onClick={() => void signIn()}
            disabled={pending}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground disabled:opacity-50"
          >
            <LogIn className="h-3 w-3" />
            {t("agent_accounts.sign_in")}
          </button>
        )}

        {!account.builtin && (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(account.label);
                setRenaming(true);
              }}
              aria-label={t("agent_accounts.rename")}
              title={t("agent_accounts.rename")}
              className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
            >
              <Pencil className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={() =>
                void run(`delete:${account.id}`, () => deleteAgentAccount(account.id))
              }
              disabled={pending}
              aria-label={t("agent_accounts.remove")}
              title={t("agent_accounts.remove")}
              className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </>
        )}
      </div>

      <p
        className={cn(
          "flex items-start gap-1.5 pl-6 text-[11px]",
          account.connected ? "text-muted-foreground" : "text-amber-600",
        )}
      >
        <KeyRound className="mt-0.5 h-3 w-3 shrink-0" />
        <span className="min-w-0 break-words">{account.message}</span>
      </p>

      {/* Two accounts on ONE subscription look completely healthy — both rows
          green, both naming a valid plan — and the only symptom is usage
          draining twice as fast. So it is said out loud, next to the row that
          duplicates another, rather than left to be deduced from a usage page. */}
      {account.warning && (
        <p className="flex items-start gap-1.5 pl-6 text-[11px] text-amber-600">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="min-w-0 break-words">{account.warning}</span>
        </p>
      )}

      {/* How much of THIS seat's plan is gone — the number the choice above is
          actually made on, which until now could only be read by opening a
          pane and asking the CLI itself. */}
      <AccountUsageMeters usage={usage} now={now} />

      {flow && (
        <LoginFlowBox
          flow={flow}
          onFlow={setFlow}
          onClose={() => {
            if (!flow.finished) void cancelLoginFlow(flow.flow_id).catch(() => {});
            setFlow(null);
          }}
          onRetry={() => void signIn()}
          onFallback={() => void signInExternally()}
        />
      )}
    </li>
  );
}

/**
 * The in-app sign-in surface: what used to require a raw console window.
 *
 * The link is DATA here, not a redirect — with several subscriptions the
 * default browser is usually signed in as the wrong account, so the user must
 * be able to copy the URL into a private window or another profile. The code
 * field replaces pasting into a TUI, which on Windows rendered late or not at
 * all and burned the single-use code.
 */
function LoginFlowBox({
  flow,
  onFlow,
  onClose,
  onRetry,
  onFallback,
}: {
  flow: LoginFlowState;
  onFlow: (next: LoginFlowState) => void;
  onClose: () => void;
  onRetry: () => void;
  onFallback: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const failed = flow.status === "failed" || flow.status === "cancelled";

  async function copyUrl() {
    if (!flow.url) return;
    if (await robustCopy(flow.url)) {
      pushToast("success", t("agent_accounts.flow.copied"));
    } else {
      pushToast("error", t("agent_accounts.flow.copy_failed"));
    }
  }

  async function pasteCode() {
    const text = await robustPaste();
    if (text) setCode(text.trim());
  }

  async function submit() {
    const value = code.trim();
    if (!value || sending) return;
    setSending(true);
    try {
      onFlow(await submitLoginFlowCode(flow.flow_id, value));
      setCode("");
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      data-testid={`login-flow-${flow.account_id}`}
      className="ml-6 space-y-2 rounded-xl border border-border/70 bg-background/50 p-3"
    >
      <div className="flex items-center gap-2">
        {!flow.finished && (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        )}
        <span
          className={cn(
            "min-w-0 flex-1 break-words text-[11px]",
            failed ? "text-amber-600" : "text-muted-foreground",
          )}
        >
          {flow.status === "starting"
            ? t("agent_accounts.flow.starting")
            : flow.status === "awaiting_input" && !flow.code_expected
              ? t("agent_accounts.flow.waiting_browser")
              : flow.message || t("agent_accounts.flow.starting")}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("agent_accounts.flow.cancel")}
          title={t("agent_accounts.flow.cancel")}
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {flow.url && !failed && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <code
              data-testid="login-flow-url"
              className="min-w-0 flex-1 truncate rounded-md border border-border bg-background px-2 py-1 font-mono text-[10px] text-muted-foreground"
              title={flow.url}
            >
              {flow.url}
            </code>
            <button
              type="button"
              onClick={() => void copyUrl()}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] hover:border-primary/40"
            >
              <Copy className="h-3 w-3" />
              {t("agent_accounts.flow.copy")}
            </button>
            <button
              type="button"
              onClick={() => void openExternalUrl(flow.url!)}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] hover:border-primary/40"
            >
              <ExternalLink className="h-3 w-3" />
              {t("agent_accounts.flow.open")}
            </button>
          </div>
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            {t("agent_accounts.flow.link_hint")}
          </p>
        </div>
      )}

      {flow.code_expected && !flow.finished && (
        <div className="flex items-center gap-1.5">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
            placeholder={t("agent_accounts.flow.code_placeholder")}
            aria-label={t("agent_accounts.flow.code_placeholder")}
            spellCheck={false}
            autoComplete="off"
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1.5 font-mono text-xs"
          />
          <button
            type="button"
            onClick={() => void pasteCode()}
            aria-label={t("agent_accounts.flow.paste")}
            title={t("agent_accounts.flow.paste")}
            className="shrink-0 rounded-lg border border-border p-1.5 hover:border-primary/40"
          >
            <ClipboardPaste className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!code.trim() || sending}
            className="shrink-0 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
          >
            {t("agent_accounts.flow.submit")}
          </button>
        </div>
      )}

      {failed && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRetry}
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
          >
            {t("agent_accounts.flow.retry")}
          </button>
          <button
            type="button"
            onClick={onFallback}
            className="rounded-lg border border-border px-3 py-1.5 text-xs hover:border-primary/40"
          >
            {t("agent_accounts.flow.fallback")}
          </button>
        </div>
      )}

      {flow.tail && !flow.finished && (
        <details className="text-[10px] text-muted-foreground">
          <summary className="cursor-pointer select-none">
            {t("agent_accounts.flow.show_cli")}
          </summary>
          <pre className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-background/70 p-2">
            {flow.tail}
          </pre>
        </details>
      )}
    </div>
  );
}

export default AgentAccountsPanel;
