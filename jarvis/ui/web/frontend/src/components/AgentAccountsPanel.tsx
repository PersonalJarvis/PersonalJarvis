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

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  KeyRound,
  LogIn,
  Pencil,
  Plus,
  Trash2,
  Users,
} from "lucide-react";

import { useT } from "@/i18n";
import {
  type AccountPlatform,
  type AgentAccount,
  type AgentAccountsResponse,
  createAgentAccount,
  deleteAgentAccount,
  fetchAgentAccounts,
  groupFor,
  loginAgentAccount,
  renameAgentAccount,
  setActiveAgentAccount,
} from "@/lib/agentAccountsApi";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

const PLATFORM_LABEL: Record<AccountPlatform, string> = {
  claude: "Claude Code",
  codex: "Codex",
};

const PLATFORMS: AccountPlatform[] = ["claude", "codex"];

export function AgentAccountsPanel() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [data, setData] = useState<AgentAccountsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

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
      </div>
      <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
        {t("agent_accounts.description")}
      </p>

      {error && (
        <p className="px-1 text-[11px] text-amber-600" role="alert">
          {error}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2 md:items-start">
        {PLATFORMS.map((platform) => (
          <PlatformCard
            key={platform}
            platform={platform}
            group={groupFor(data, platform)}
            busy={busy}
            run={run}
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
}: {
  platform: AccountPlatform;
  group: ReturnType<typeof groupFor>;
  busy: string | null;
  run: (key: string, action: () => Promise<AgentAccountsResponse | void>) => Promise<void>;
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
        <h4 className="text-sm font-semibold">{PLATFORM_LABEL[platform]}</h4>
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
}: {
  account: AgentAccount;
  active: boolean;
  busy: string | null;
  run: (key: string, action: () => Promise<AgentAccountsResponse | void>) => Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(account.label);
  const pending = busy?.endsWith(account.id) ?? false;

  async function activate() {
    if (active) return;
    await run(`active:${account.id}`, () =>
      setActiveAgentAccount(account.platform, account.id),
    );
  }

  async function signIn() {
    await run(`login:${account.id}`, async () => {
      const { message } = await loginAgentAccount(account.id);
      pushToast("info", message);
    });
  }

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
          onClick={() => void activate()}
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

        {!account.connected && (
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

      {/* Two accounts on ONE subscription look completely healthy â€” both rows
          green, both naming a valid plan â€” and the only symptom is usage
          draining twice as fast. So it is said out loud, next to the row that
          duplicates another, rather than left to be deduced from a usage page. */}
      {account.warning && (
        <p className="flex items-start gap-1.5 pl-6 text-[11px] text-amber-600">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="min-w-0 break-words">{account.warning}</span>
        </p>
      )}
    </li>
  );
}

export default AgentAccountsPanel;
