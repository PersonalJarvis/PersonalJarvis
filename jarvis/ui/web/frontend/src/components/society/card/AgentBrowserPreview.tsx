/**
 * The agent's browser, as a small screen on the chat-face Options rail.
 *
 * There is no live Chrome frame yet (runs are headless; the runner emits
 * step URLs, not pixels). This box is honest about that: not set up, idle,
 * signing in (a real OS window), or connecting while a run is in flight.
 * The 16:10 rounded well is the slot a later screencast can occupy.
 */
import { useState } from "react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import {
  useAgentBrowser,
  useAgentBrowserLogin,
  useBrowserInstallStatus,
  useStartBrowserInstall,
} from "../cardData";
import type { SocietyAgent } from "../data";

import "./agentCard.css";

export interface AgentBrowserPreviewProps {
  agent: SocietyAgent;
}

function PreviewFace({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={cn("h-12 w-12", className)} aria-hidden>
      <circle cx="24" cy="26" r="4.2" fill="currentColor" />
      <circle cx="40" cy="26" r="4.2" fill="currentColor" />
      <path
        d="M22 40c3.6 6.2 16.4 6.2 20 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function AgentBrowserPreview({ agent }: AgentBrowserPreviewProps) {
  const t = useT();
  const browser = useAgentBrowser(agent.agentId);
  const install = useBrowserInstallStatus();
  const startInstall = useStartBrowserInstall();
  const login = useAgentBrowserLogin();
  const [busy, setBusy] = useState<"install" | "login" | null>(null);
  const [error, setError] = useState("");

  const installed = Boolean(browser.data?.installed || install.data?.installed);
  const running = Boolean(browser.data?.running);
  const loggedIn = Boolean(browser.data?.loggedIn);
  const installing = Boolean(install.data && (install.data.running || (install.data.phase !== "idle" && install.data.phase !== "done" && install.data.phase !== "error" && !install.data.installed)));
  const percent = install.data?.percent ?? 0;
  const installError = install.data?.error ?? "";

  const status = installing
    ? t("society.card.browser_setting_up")
    : running
      ? t("society.card.browser_connecting")
      : !installed
        ? t("society.card.browser_not_setup")
        : t("society.card.browser_idle");

  const onSetup = async () => {
    setBusy("install");
    setError("");
    try {
      await startInstall();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("society.card.browser_error"));
    } finally {
      setBusy(null);
    }
  };

  const onSignIn = () => {
    setBusy("login");
    setError("");
    login(agent.agentId);
    setBusy(null);
  };

  return (
    <div className="shrink-0" data-testid="agent-browser-preview">
      <div
        className="or-screen flex flex-col items-center justify-center gap-2 px-4"
        data-busy={running || installing ? "1" : undefined}
      >
        <PreviewFace className="or-screen-mark" />
        <p className="max-w-[16rem] text-center text-[11px] leading-snug text-muted-foreground">
          {installing && install.data?.detail ? install.data.detail : status}
        </p>
        {installing ? (
          <span className="or-screen-bar" aria-hidden>
            <span style={{ width: `${Math.max(4, Math.min(100, percent))}%` }} />
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 truncate text-center text-[11px] text-muted-foreground" title={agent.name}>
        {t("society.card.screen_of").replace("{0}", agent.name)}
      </p>
      <div className="mt-1.5 flex flex-col items-center gap-1">
        {!installed && !installing ? (
          <button
            type="button"
            className="text-[11px] font-medium text-foreground underline-offset-2 hover:underline disabled:opacity-50"
            disabled={busy !== null}
            onClick={() => void onSetup()}
            data-testid="agent-browser-setup"
          >
            {busy === "install" ? t("society.card.browser_setting_up") : t("society.card.browser_setup")}
          </button>
        ) : null}
        {installed && !loggedIn && !running ? (
          <button
            type="button"
            className="text-[11px] font-medium text-foreground underline-offset-2 hover:underline disabled:opacity-50"
            disabled={busy !== null}
            title={t("society.card.browser_sign_in_hint")}
            onClick={onSignIn}
            data-testid="agent-browser-login"
          >
            {t("society.card.browser_sign_in")}
          </button>
        ) : null}
        {(error || installError) && (
          <p className="text-center text-[11px] text-destructive">{error || installError}</p>
        )}
      </div>
    </div>
  );
}

export default AgentBrowserPreview;
