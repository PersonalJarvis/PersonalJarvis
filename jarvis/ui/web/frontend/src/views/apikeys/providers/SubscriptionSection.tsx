import { useState } from "react";
import { FlaskConical, Loader2, LogIn, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  codexLogout,
  loginAntigravity,
  loginClaude,
  loginGrokBuild,
  logoutAntigravity,
  logoutClaude,
  logoutGrokBuild,
  startCodexLogin,
  testAgentCli,
  type AgentCliTestResult,
  type ProviderDescriptor,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import {
  pollUntilConnected,
  subscriptionStatusUrl,
  type ProviderFamily,
  type SubscriptionKind,
} from "@/lib/providerFamilies";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { ProfileGroup, SettingRow } from "@/views/profile/ProfileGroup";
import type { FamilyState } from "./familyState";

/** The four subscription logins, each driven by the vendor's own CLI. */
const LOGIN: Record<
  SubscriptionKind,
  { login: () => Promise<void>; logout: () => Promise<void>; test: string; where: "browser" | "terminal" }
> = {
  codex: { login: () => startCodexLogin(), logout: () => codexLogout(), test: "/api/codex/test", where: "browser" },
  claude_cli: { login: loginClaude, logout: logoutClaude, test: "/api/claude/test", where: "terminal" },
  antigravity: { login: loginAntigravity, logout: logoutAntigravity, test: "/api/antigravity/test", where: "browser" },
  grok_build: { login: loginGrokBuild, logout: logoutGrokBuild, test: "/api/grok-build/test", where: "browser" },
};

/**
 * Sign in with the plan the user already pays for. One row says whose plan is
 * connected and offers the one next step (connect, finish, disconnect); a
 * second row names the command-line tool behind it, the way a terminal user
 * would check it, with a live test.
 */
export function SubscriptionSection({
  family,
  state,
  onChanged,
}: {
  family: ProviderFamily;
  state: FamilyState;
  onChanged: () => void | Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState<"login" | "logout" | "waiting" | null>(null);
  const subscription = family.subscription!;
  const flow = LOGIN[subscription.kind];
  const status = state.subscription;
  const card: ProviderDescriptor | undefined = state.members.find(
    (m) => m.id === subscription.provider_id,
  );
  const installed = status?.installed ?? false;
  const version = status?.version ?? null;
  const binary = status && "binary_path" in status ? status.binary_path ?? null : null;

  async function connect() {
    setPending("login");
    try {
      await flow.login();
      pushToast(
        "info",
        t(flow.where === "browser" ? "providers_page.login_started_browser" : "providers_page.login_started_terminal"),
      );
      setPending("waiting");
      await onChanged();
      const ok = await pollUntilConnected(subscriptionStatusUrl(subscription.kind), onChanged);
      if (ok) pushToast("success", t("providers_page.login_done").replace("{0}", family.label));
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function disconnect() {
    setPending("logout");
    try {
      await flow.logout();
      pushToast("info", t("providers_page.logout_done").replace("{0}", family.label));
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setPending(null);
    }
  }

  const hint = !status
    ? t("providers_page.subscription_unknown")
    : state.subscriptionOn
      ? state.account
        ? t("providers_page.subscription_signed_in").replace("{0}", state.account)
        : status.message
      : !installed
        ? card?.install_hint || t("providers_page.subscription_install")
        : pending === "waiting"
          ? t(flow.where === "browser" ? "providers_page.login_waiting_browser" : "providers_page.login_waiting_terminal")
          : t("providers_page.subscription_hint");

  return (
    <ProfileGroup
      title={t("providers_page.subscription_title")}
      description={t("providers_page.subscription_desc").replace("{0}", family.label)}
      testId="provider-subscription"
    >
      <SettingRow
        label={
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden="true"
              className={cn(
                "h-2 w-2 rounded-full",
                state.subscriptionOn ? "bg-success" : "bg-border-strong",
              )}
            />
            {subscription.label}
          </span>
        }
        hint={hint}
        control={
          state.subscriptionOn ? (
            <Button
              size="sm"
              variant="outline"
              data-testid="provider-subscription-disconnect"
              disabled={pending !== null}
              onClick={() => void disconnect()}
            >
              {pending === "logout" ? <Loader2 className="animate-spin" /> : <LogOut />}
              {t("providers_page.disconnect")}
            </Button>
          ) : (
            <Button
              size="sm"
              data-testid="provider-subscription-connect"
              disabled={pending !== null || !installed}
              onClick={() => void connect()}
            >
              {pending === "login" || pending === "waiting" ? <Loader2 className="animate-spin" /> : <LogIn />}
              {t("providers_page.connect")}
            </Button>
          )
        }
      />
      {installed && (
        <SettingRow
          label={t("providers_page.cli_label")}
          hint={
            <span className="break-all">
              {[version, binary].filter(Boolean).join(" · ") || t("providers_page.cli_found")}
            </span>
          }
          control={<CliTest endpoint={flow.test} onChanged={onChanged} />}
        />
      )}
    </ProfileGroup>
  );
}

/** A live check of the CLI: found where, which version, signed in as whom. */
function CliTest({ endpoint, onChanged }: { endpoint: string; onChanged: () => void | Promise<void> }) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentCliTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await testAgentCli(endpoint));
      await onChanged();
    } catch (cause) {
      setResult(null);
      setError((cause as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <span className="flex items-center gap-3">
      {(result || error) && (
        <span
          role="status"
          title={error ?? result?.message}
          className={cn(
            "max-w-[16rem] truncate text-sm",
            error || (result && !result.ok) ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {error ?? result?.message}
        </span>
      )}
      <Button size="sm" variant="ghost" disabled={running} onClick={() => void run()}>
        {running ? <Loader2 className="animate-spin" /> : <FlaskConical />}
        {t("providers_page.test")}
      </Button>
    </span>
  );
}
