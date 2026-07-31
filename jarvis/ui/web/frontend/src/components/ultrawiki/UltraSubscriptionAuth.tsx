/**
 * Subscription-login control shared by the UltraWiki distillation cards.
 *
 * The three supported CLI brains expose the same capability (installed,
 * connected, account identity) through different auth endpoints. Branching on
 * the declared auth mode keeps provider names out of the component (AP-21),
 * while reusing the established login/logout actions from the Jarvis-Agent UI.
 */
import { useCallback, useEffect, useState } from "react";
import { Loader2, LogIn, LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  codexLogout,
  loginAntigravity,
  loginClaude,
  logoutAntigravity,
  logoutClaude,
  startCodexLogin,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import type { UltraWikiAuthMode } from "@/lib/ultrawikiApi";
import { useEventStore } from "@/store/events";

type SubscriptionAuthMode = Extract<
  UltraWikiAuthMode,
  "codex" | "antigravity" | "claude_cli"
>;

interface SubscriptionStatus {
  installed: boolean;
  connected: boolean;
  mode: string;
  user_email?: string | null;
  account_label?: string | null;
  accountLabel?: string | null;
}

interface AuthDriver {
  statusUrl: string;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  isSubscriptionConnected: (status: SubscriptionStatus) => boolean;
}

const AUTH_DRIVERS: Record<SubscriptionAuthMode, AuthDriver> = {
  codex: {
    statusUrl: "/api/codex/status",
    connect: startCodexLogin,
    disconnect: codexLogout,
    isSubscriptionConnected: (status) =>
      status.connected && status.mode === "chatgpt",
  },
  antigravity: {
    statusUrl: "/api/antigravity/status",
    connect: loginAntigravity,
    disconnect: logoutAntigravity,
    isSubscriptionConnected: (status) =>
      status.connected && status.mode === "oauth-personal",
  },
  claude_cli: {
    statusUrl: "/api/claude/status",
    connect: loginClaude,
    disconnect: logoutClaude,
    isSubscriptionConnected: (status) =>
      status.connected && status.mode === "subscription",
  },
};

export function isSubscriptionAuthMode(
  mode: UltraWikiAuthMode,
): mode is SubscriptionAuthMode {
  return mode in AUTH_DRIVERS;
}

async function fetchStatus(driver: AuthDriver): Promise<SubscriptionStatus> {
  const response = await fetch(driver.statusUrl);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as SubscriptionStatus;
}

export function UltraSubscriptionAuth({
  authMode,
  onChanged,
}: {
  authMode: SubscriptionAuthMode;
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const driver = AUTH_DRIVERS[authMode];
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [statusError, setStatusError] = useState(false);

  const reload = useCallback(async (): Promise<SubscriptionStatus | null> => {
    try {
      const next = await fetchStatus(driver);
      setStatus(next);
      setStatusError(false);
      return next;
    } catch {
      setStatusError(true);
      return null;
    }
  }, [driver]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const connected = Boolean(
    status && driver.isSubscriptionConnected(status),
  );
  const account =
    status?.user_email ?? status?.account_label ?? status?.accountLabel ?? null;

  async function pollUntilConnected(): Promise<void> {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 2_500));
      const next = await reload();
      if (next && driver.isSubscriptionConnected(next)) {
        pushToast("success", t("ultrawiki.subscription.connected"));
        onChanged();
        return;
      }
    }
  }

  async function connect(): Promise<void> {
    setPending(true);
    try {
      await driver.connect();
      pushToast("info", t("ultrawiki.subscription.login_started"));
      onChanged();
      void pollUntilConnected();
    } catch (error) {
      pushToast(
        "error",
        t("ultrawiki.subscription.login_failed").replace(
          "{0}",
          (error as Error).message,
        ),
      );
    } finally {
      setPending(false);
    }
  }

  async function disconnect(): Promise<void> {
    setPending(true);
    try {
      await driver.disconnect();
      await reload();
      onChanged();
      pushToast("info", t("ultrawiki.subscription.disconnected"));
    } catch (error) {
      pushToast(
        "error",
        t("ultrawiki.subscription.disconnect_failed").replace(
          "{0}",
          (error as Error).message,
        ),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className="space-y-2 rounded-md border border-border/60 bg-muted/20 p-2.5"
      data-testid={`ultrawiki-subscription-${authMode}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          {connected
            ? account
              ? t("ultrawiki.subscription.connected_as").replace("{0}", account)
              : t("ultrawiki.subscription.connected")
            : status?.installed === false
              ? t("ultrawiki.subscription.cli_missing")
              : statusError
                ? t("ultrawiki.subscription.status_unavailable")
                : t("ultrawiki.subscription.not_connected")}
        </p>
        {connected ? (
          <Button
            size="sm"
            variant="outline"
            disabled={pending}
            onClick={() => void disconnect()}
            className="h-7 gap-1.5 text-xs"
            data-testid={`ultrawiki-subscription-disconnect-${authMode}`}
          >
            {pending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <LogOut className="h-3.5 w-3.5" />
            )}
            {t("ultrawiki.subscription.disconnect")}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            disabled={pending || status?.installed === false}
            onClick={() => void connect()}
            className="h-7 gap-1.5 text-xs"
            data-testid={`ultrawiki-subscription-connect-${authMode}`}
          >
            {pending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <LogIn className="h-3.5 w-3.5" />
            )}
            {t("ultrawiki.subscription.connect")}
          </Button>
        )}
      </div>
    </div>
  );
}
