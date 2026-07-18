import { useCallback, useEffect, useRef, useState } from "react";
import { Download, RotateCw } from "lucide-react";

import { useEventStore } from "@/store/events";
import { useUpdate } from "@/hooks/useUpdate";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Global top bar rendered above every view (see App.tsx). It carries the app-
 * chrome actions that must be reachable from *every* screen: a "Restart Jarvis"
 * button, and — only when a newer version is published — an "Update" button that
 * pulls the new code and restarts, so an end user never touches a terminal.
 *
 * Why a global bar and not the per-view ``ViewHeader``: a couple of views
 * (DocsView's 3-column layout, SubAgentsView's departure board) don't render a
 * ViewHeader at all, and ~13 views already fill the header's ``right`` slot with
 * their own actions. Putting the buttons in the shell, above MainView, is the
 * only placement that is on every screen and collides with nothing.
 *
 * The backend already ships the whole self-restart machinery: both buttons POST
 * to the existing ``/api/settings/restart-app`` endpoint, which spawns a
 * detached, cross-platform relauncher (see jarvis/ui/relauncher.py). On a
 * headless host that endpoint returns 503 and we recover with an honest toast.
 *
 * A restart tears down the whole app mid-task, so both buttons honor the mission
 * guard (409 → arm a force override) rather than killing live missions silently.
 * We avoid a native ``window.confirm`` on purpose — it blocks the pywebview loop.
 */
const CONFIRM_TIMEOUT_MS = 4000;

export function TopBar() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [confirming, setConfirming] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Set when the backend refused the restart (HTTP 409) because missions are
  // running: the next click resends the POST with ``force=true``.
  const [forceArmed, setForceArmed] = useState(false);
  const resetTimer = useRef<number | null>(null);

  const clearResetTimer = useCallback(() => {
    if (resetTimer.current !== null) {
      clearTimeout(resetTimer.current);
      resetTimer.current = null;
    }
  }, []);

  // Drop a pending "disarm" timer if the bar is ever unmounted.
  useEffect(() => clearResetTimer, [clearResetTimer]);

  async function doRestart(force: boolean) {
    clearResetTimer();
    setRestarting(true);
    try {
      const url = force
        ? "/api/settings/restart-app?force=true"
        : "/api/settings/restart-app";
      const res = await fetch(url, { method: "POST" });
      if (res.status === 409) {
        // The mission guard refused: a restart would kill live missions. Don't
        // kill them silently — surface the count and arm a force-restart so the
        // next click is the user's explicit override.
        let count = 0;
        try {
          const body = await res.json();
          count = body?.detail?.missions?.length ?? 0;
        } catch {
          /* malformed body — still arm the override */
        }
        setRestarting(false);
        setConfirming(false);
        setForceArmed(true);
        clearResetTimer();
        resetTimer.current = window.setTimeout(() => {
          setForceArmed(false);
          resetTimer.current = null;
        }, CONFIRM_TIMEOUT_MS);
        pushToast("warning", `${count} ${t("topbar.restart_missions_running")}`);
        return;
      }
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      // On success the window goes away — keep the spinning state; never clear
      // it, so the user doesn't see the button flip back before the app dies.
      pushToast("info", t("topbar.restarting"));
    } catch {
      // Headless host (503) or a transient failure: recover the control so the
      // user isn't left with a dead button.
      setRestarting(false);
      setConfirming(false);
      setForceArmed(false);
      pushToast("error", t("topbar.restart_failed"));
    }
  }

  function onClick() {
    if (restarting) return;
    if (forceArmed) {
      // The guard already refused once; this click is the explicit override.
      void doRestart(true);
      return;
    }
    if (!confirming) {
      // First click only arms the confirmation; auto-disarm after a few
      // seconds so a stray click never leaves a primed restart button behind.
      setConfirming(true);
      clearResetTimer();
      resetTimer.current = window.setTimeout(() => {
        setConfirming(false);
        resetTimer.current = null;
      }, CONFIRM_TIMEOUT_MS);
      return;
    }
    void doRestart(false);
  }

  const label = restarting
    ? t("topbar.restarting")
    : forceArmed
      ? t("topbar.restart_force")
      : confirming
        ? t("topbar.restart_confirm")
        : t("topbar.restart");

  return (
    <div className="flex h-10 shrink-0 items-center justify-end gap-2 border-b border-border bg-background/70 px-4 backdrop-blur-sm">
      <UpdateButton />
      <button
        type="button"
        onClick={onClick}
        disabled={restarting}
        title={t("topbar.restart_hint")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-70",
          confirming || forceArmed
            ? "border-amber-500/60 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
            : "border-border bg-secondary/40 text-muted-foreground hover:border-primary/50 hover:text-foreground",
        )}
      >
        <RotateCw
          aria-hidden
          className={cn("h-3.5 w-3.5", restarting && "animate-spin")}
        />
        {label}
      </button>
    </div>
  );
}

// The restart can fail transiently right after an apply (the backend is busy,
// the desktop shell still warming) — retry briefly before giving up honestly.
const RESTART_ATTEMPTS = 3;
const RESTART_RETRY_MS = 1500;

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Append the server's error detail so a failure is diagnosable, not generic. */
function withDetail(message: string, detail: string | null): string {
  const trimmed = (detail ?? "").trim();
  if (!trimmed) return message;
  return `${message} (${trimmed.slice(0, 180)})`;
}

/**
 * Shown when the backend reports a managed install with a newer published
 * release (``status.update_available``) — or with a staged-but-not-installed
 * transaction (``status.pending_update``) left behind by an earlier attempt.
 * One click stages the new code (`POST /api/update/apply`) and then restarts
 * to install it, reusing the same mission-guard (409 → force) flow as the
 * restart button. Hovering reveals the release notes. On a dev tree / manual
 * clone the status is ``managed: false``, so this renders nothing and can
 * never trigger a self-update.
 */
function UpdateButton() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { status } = useUpdate();
  const [busy, setBusy] = useState(false);
  const [forceArmed, setForceArmed] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const resetTimer = useRef<number | null>(null);
  const rollbackNotified = useRef<string | null>(null);

  const clearResetTimer = useCallback(() => {
    if (resetTimer.current !== null) {
      clearTimeout(resetTimer.current);
      resetTimer.current = null;
    }
  }, []);
  useEffect(() => clearResetTimer, [clearResetTimer]);

  // A rolled-back install is otherwise invisible: the app comes back on the
  // old version and the button simply re-renders. Say it happened, once.
  const lastResult = status?.last_result;
  useEffect(() => {
    if (!lastResult || lastResult.ok) return;
    const key = String(lastResult.completed_at ?? "unknown");
    if (rollbackNotified.current === key) return;
    rollbackNotified.current = key;
    pushToast("warning", t("topbar.update_rolled_back"));
  }, [lastResult, pushToast, t]);

  if (!status?.managed) return null;
  const hasOffer = status.update_available;
  const hasStaged = Boolean(status.pending_update);
  if (!hasOffer && !hasStaged) return null;
  const shownVersion = status.latest ?? status.pending_update?.version ?? null;

  async function run(force: boolean) {
    clearResetTimer();
    setBusy(true);
    setShowNotes(false);

    // 1. Stage the new code. The server re-verifies the managed-install guard,
    // so a spoofed client can't force a reset on an unmanaged checkout. This is
    // idempotent — with a transaction already staged it succeeds even offline.
    try {
      const applyRes = await fetch("/api/update/apply", { method: "POST" });
      const applyBody = (await applyRes.json().catch(() => ({}))) as {
        detail?: string;
        deps_warning?: string | null;
        desktop_integration_warning?: string | null;
      };
      if (!applyRes.ok) {
        // 403 unmanaged / 409 nothing newer / 502 git or GitHub failure — the
        // backend's detail says WHICH, and the user must get to see it.
        setBusy(false);
        setForceArmed(false);
        pushToast(
          "error",
          withDetail(
            t("topbar.update_failed"),
            applyBody.detail ?? `HTTP ${applyRes.status}`,
          ),
        );
        return;
      }
      if (applyBody.deps_warning) {
        pushToast("warning", t("topbar.update_deps_warning"));
      }
      if (applyBody.desktop_integration_warning) {
        pushToast("warning", t("topbar.update_desktop_warning"));
      }
    } catch {
      setBusy(false);
      setForceArmed(false);
      pushToast(
        "error",
        withDetail(t("topbar.update_failed"), t("topbar.update_network_error")),
      );
      return;
    }

    // 2. Restart to install it — reuse the existing route + its mission guard,
    // retrying a transient failure before giving up. The update stays staged
    // either way, so the honest fallback is "restart to finish", not "failed".
    let restartDetail: string | null = null;
    for (let attempt = 0; attempt < RESTART_ATTEMPTS; attempt++) {
      if (attempt > 0) await wait(RESTART_RETRY_MS);
      try {
        const url = force
          ? "/api/settings/restart-app?force=true"
          : "/api/settings/restart-app";
        const restartRes = await fetch(url, { method: "POST" });
        if (restartRes.status === 409) {
          let count = 0;
          try {
            const body = await restartRes.json();
            count = body?.detail?.missions?.length ?? 0;
          } catch {
            /* malformed body — still arm the override */
          }
          setBusy(false);
          setForceArmed(true);
          clearResetTimer();
          resetTimer.current = window.setTimeout(() => {
            setForceArmed(false);
            resetTimer.current = null;
          }, CONFIRM_TIMEOUT_MS);
          pushToast(
            "warning",
            `${count} ${t("topbar.restart_missions_running")}`,
          );
          return;
        }
        if (restartRes.ok) {
          // Success — the code is staged and the window goes away shortly; keep
          // the busy state so the button never flips back before the app dies.
          pushToast("info", t("topbar.updating"));
          return;
        }
        const body = (await restartRes.json().catch(() => ({}))) as {
          detail?: unknown;
        };
        restartDetail =
          typeof body.detail === "string"
            ? body.detail
            : `HTTP ${restartRes.status}`;
      } catch {
        restartDetail = t("topbar.update_network_error");
      }
    }
    // The download IS staged — a manual restart (or the next successful click)
    // finishes it. Saying "failed" here would be dishonest and untraceable.
    setBusy(false);
    setForceArmed(false);
    pushToast(
      "warning",
      withDetail(t("topbar.update_staged_restart_failed"), restartDetail),
    );
  }

  function onClick() {
    if (busy) return;
    void run(forceArmed);
  }

  const label = busy
    ? t("topbar.updating")
    : forceArmed
      ? t("topbar.restart_force")
      : hasOffer
        ? t("topbar.update_available")
        : t("topbar.update_finish_restart");

  return (
    <div
      className="relative"
      onMouseEnter={() => !busy && status.notes && setShowNotes(true)}
      onMouseLeave={() => setShowNotes(false)}
    >
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title={t("topbar.update_hint")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-70",
          forceArmed
            ? "border-amber-500/60 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
            : "border-primary/50 bg-primary/10 text-primary hover:bg-primary/20",
        )}
      >
        <Download
          aria-hidden
          className={cn("h-3.5 w-3.5", busy && "animate-pulse")}
        />
        {label}
        {!busy && !forceArmed && shownVersion && (
          <span className="rounded bg-primary/20 px-1 text-[10px] tabular-nums">
            v{shownVersion}
          </span>
        )}
      </button>
      {showNotes && status.notes && (
        <div className="absolute right-0 top-full z-50 mt-1 w-80 rounded-md border border-border bg-background p-3 text-left shadow-lg">
          <div className="mb-1 text-xs font-semibold text-foreground">
            {t("topbar.update_available")} · v{shownVersion}
          </div>
          <div className="max-h-64 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
            {status.notes.slice(0, 800)}
          </div>
        </div>
      )}
    </div>
  );
}
