import { useCallback, useEffect, useRef, useState } from "react";
import { AppWindow, Download, RotateCw } from "lucide-react";

import { useEventStore, type SectionId } from "@/store/events";
import {
  fetchUpdateProgress,
  useUpdate,
  type UpdateProgress,
} from "@/hooks/useUpdate";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { hasEmbeddedDesktopBridge } from "@/components/voice/BrowserRealtimeControl";
import { openExternalUrl } from "@/lib/openExternal";

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
 *
 * **Two surfaces render the bar themselves.** The Agentic IDE is a wall of
 * terminal output with a single header row of its own, and a second full-width
 * bar above it holding two buttons cost that view ~40 px it had better uses
 * for. The mission deck has the same shape: its HUD is already a header, and
 * an empty chrome strip above it was a second, boring row. Both take
 * `TopBarActions` into their own row and this bar steps aside there — the
 * actions are still on that screen, which is the rule that matters; what
 * moved is the furniture around them. Every other view keeps the bar.
 */
const CONFIRM_TIMEOUT_MS = 4000;

const CODING_SECTIONS = new Set<SectionId>([
  "agentic-ide",
  "agentic-ide-classic",
  "chat-workspace",
]);

export function TopBar() {
  const activeSection = useEventStore((s) => s.activeSection);
  const solo = useEventStore((s) => s.solo);
  const detachedViews = useEventStore((s) => s.detachedViews);
  // The coding workspace takes the actions into its own toolbar row, under
  // every id that reaches it. The rule is "the section carries the actions
  // itself" — a section that does not would lose Restart, which is the one
  // control a frontend change is reached through. Once detached, that toolbar
  // lives in the solo window and the main window only has a placeholder, so
  // the main shell must provide the global bar again.
  const codingDetached =
    !solo && detachedViews.some((view) => CODING_SECTIONS.has(view));
  if (CODING_SECTIONS.has(activeSection) && !codingDetached) {
    return null;
  }

  /*
   * The front page already has a header. A second empty strip above it was
   * the boring row. Same rule as the IDE: this bar steps aside, the actions
   * move into that header (`HomeView`). While chats is detached the main
   * window is a placeholder and needs the bar back.
   */
  const chatsDetached = !solo && detachedViews.includes("chats");
  if (activeSection === "chats" && !chatsDetached) {
    return null;
  }

  return (
    <div className="jarvis-shell-surface flex h-10 shrink-0 items-center justify-end gap-2 border-b border-border px-4 backdrop-blur-md">
      {/* Status, not an action — `mr-auto` pins it left so it never crowds
          the buttons. The deck header does not pass this: it already sits
          in a right-hand cluster. */}
      <CodingModeBadge className="mr-auto" />
      <TopBarActions />
    </div>
  );
}

/**
 * The app-chrome ACTIONS, without the bar around them.
 *
 * Separate from `TopBar` so a view that already has a header row can carry them
 * in it rather than under a second one. They are the same components either
 * way — a screen that had its own copy of "restart the app" would drift from
 * this one within a release.
 */
export function TopBarActions() {
  return (
    <>
      <ThemeToggle />
      <DetachButton />
      <UpdateButton />
      <RestartButton />
    </>
  );
}

/**
 * The views the desktop shell can split off into their own solo window.
 * Mirrors the backend ``DETACHABLE_VIEWS`` registry (jarvis/ui/desktop_app.py)
 * — the server validates again, this set only decides where the button shows.
 */
const DETACHABLE_SECTIONS = new Set<SectionId>([
  "agentic-ide",
  "agentic-ide-classic",
  "chat-workspace",
  "chats",
  "dictation",
  "dictionary",
  "voice-shortcuts",
  "voice-language",
  "voice-api-keys",
  "visualization",
]);

/**
 * "Open this view in its own window" — the detach entry point.
 *
 * Bridge-first like `openExternalUrl`: inside the embedded desktop shell the
 * backend spawns a real second pywebview window (WebView2 silently drops
 * `window.open`, so the frontend cannot do it itself); in a plain browser the
 * solo URL simply opens as a new tab. A shell whose webview backend cannot
 * create runtime windows answers `ok: false` with a fallback URL and the view
 * opens in the user's real browser instead — honest on every host. Idempotent:
 * re-clicking while detached focuses the existing window.
 */
function DetachButton() {
  const t = useT();
  const activeSection = useEventStore((s) => s.activeSection);
  const solo = useEventStore((s) => s.solo);
  const pushToast = useEventStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);

  // A solo window never detaches further, and most sections have no solo mode.
  if (solo || !DETACHABLE_SECTIONS.has(activeSection)) return null;

  async function onClick() {
    if (busy) return;
    const view = activeSection;
    const soloPath = `/?view=${view}&solo=1`;
    if (!hasEmbeddedDesktopBridge()) {
      // A real browser: a tab of this same browser IS the detached window.
      window.open(soloPath, "_blank", "noopener,noreferrer");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/window/detach", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ view }),
      });
      const data = (await res.json().catch(() => null)) as {
        ok?: boolean;
        fallback_url?: string;
      } | null;
      if (data?.ok) return;
      // The shell could not create a runtime window — open a browser tab.
      await openExternalUrl(
        `${window.location.origin}${data?.fallback_url ?? soloPath}`,
      );
    } catch {
      pushToast("error", t("topbar.detach_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void onClick()}
      disabled={busy}
      title={t("topbar.detach_hint")}
      data-testid="detach-view-button"
      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-secondary/40 px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:cursor-default disabled:opacity-70"
    >
      <AppWindow aria-hidden className="h-3.5 w-3.5" />
      {t("topbar.detach")}
    </button>
  );
}

function RestartButton() {
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
    <button
      type="button"
      onClick={onClick}
      disabled={restarting}
      title={t("topbar.restart_hint")}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-70",
        confirming || forceArmed
          ? "border-foreground/60 bg-foreground/10 text-foreground hover:bg-foreground/20"
          : "border-border bg-secondary/40 text-muted-foreground hover:border-primary/50 hover:text-foreground",
      )}
    >
      <RotateCw
        aria-hidden
        className={cn("h-3.5 w-3.5", restarting && "animate-spin")}
      />
      {label}
    </button>
  );
}

// The restart can fail transiently right after an apply (the backend is busy,
// the desktop shell still warming) — retry briefly before giving up honestly.
const RESTART_ATTEMPTS = 3;
const RESTART_RETRY_MS = 1500;

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// Fast enough that a 400 MB download visibly moves, slow enough to be free:
// the endpoint only copies a dict.
const PROGRESS_POLL_MS = 400;

// Which update verdict has already been announced. Stored per browser profile
// because the verdict file itself survives on disk — without this, every launch
// would re-announce the same finished update.
const NOTIFIED_UPDATE_KEY = "jarvis.update.notified";

function readNotifiedUpdate(): string | null {
  try {
    return window.localStorage.getItem(NOTIFIED_UPDATE_KEY);
  } catch {
    // Private mode / blocked site data. Losing the dedupe means one repeated
    // toast, which is strictly better than losing the message entirely.
    return null;
  }
}

function rememberNotifiedUpdate(key: string): void {
  try {
    window.localStorage.setItem(NOTIFIED_UPDATE_KEY, key);
  } catch {
    // Same trade as above — the announcement already happened.
  }
}

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
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [restarting, setRestarting] = useState(false);
  const resetTimer = useRef<number | null>(null);
  const rollbackNotified = useRef<string | null>(null);

  // Poll the progress side channel for as long as the apply request is in
  // flight. It stops on its own when the button leaves the busy state, and a
  // failed fetch is left alone: during the restart the server is *supposed* to
  // go away, and blanking the bar then would look like the update collapsed.
  useEffect(() => {
    if (!busy) return;
    let live = true;
    const tick = async () => {
      const next = await fetchUpdateProgress();
      if (live && next) setProgress(next);
    };
    void tick();
    const id = window.setInterval(() => void tick(), PROGRESS_POLL_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [busy]);

  const clearResetTimer = useCallback(() => {
    if (resetTimer.current !== null) {
      clearTimeout(resetTimer.current);
      resetTimer.current = null;
    }
  }, []);
  useEffect(() => clearResetTimer, [clearResetTimer]);

  // How the LAST update ended is otherwise invisible: the app just comes back,
  // on the new version or the old one, and the button re-renders. Both outcomes
  // get exactly one message.
  //
  // The dedupe key is remembered in localStorage, not only in this ref: the
  // verdict file survives on disk, so a ref alone would re-announce the same
  // update on every launch until the next one overwrote it.
  const lastResult = status?.last_result;
  useEffect(() => {
    if (!lastResult) return;
    const key = `${lastResult.ok ? "ok" : "fail"}:${lastResult.completed_at ?? "unknown"}`;
    if (rollbackNotified.current === key) return;
    if (readNotifiedUpdate() === key) return;
    rollbackNotified.current = key;
    rememberNotifiedUpdate(key);
    if (lastResult.ok) {
      pushToast(
        "success",
        fill(t("topbar.update_installed"), { version: status?.current ?? "" }),
      );
    } else {
      pushToast("warning", t("topbar.update_rolled_back"));
    }
  }, [lastResult, pushToast, status?.current, t]);

  if (!status?.managed) return null;
  const hasOffer = status.update_available;
  const hasStaged = Boolean(status.pending_update);
  if (!hasOffer && !hasStaged) return null;
  const shownVersion = status.latest ?? status.pending_update?.version ?? null;

  async function run(force: boolean) {
    clearResetTimer();
    setBusy(true);
    setRestarting(false);
    setProgress(null);
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
        setProgress(null);
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
      setProgress(null);
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
          setProgress(null);
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
          // This last phase is the one the server cannot report: it is the
          // thing that is dying, so the UI states it itself.
          setRestarting(true);
          pushToast("info", t("topbar.update_restarting"));
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
    setRestarting(false);
    setProgress(null);
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

  // The bar is driven by the server while it works, then pinned at 100 % for
  // the restart — the one phase nothing can measure, because the thing that
  // would report it is the thing shutting down.
  const percent = restarting ? 100 : (progress?.percent ?? 0);
  const label = restarting
    ? t("topbar.update_restarting")
    : busy
      ? fill(t("topbar.updating_percent"), { percent })
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
        title={busy ? (progress?.detail ?? label) : t("topbar.update_hint")}
        // The button doubles as the progress bar while it runs, so it carries
        // the ARIA role of one — a screen reader announces the same percentage
        // the fill shows. Outside an update those attributes must be absent,
        // not zeroed, or it reads as a bar stuck at 0 %.
        role={busy ? "progressbar" : undefined}
        aria-valuemin={busy ? 0 : undefined}
        aria-valuemax={busy ? 100 : undefined}
        aria-valuenow={busy ? percent : undefined}
        className={cn(
          "relative inline-flex items-center gap-1.5 overflow-hidden rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-70",
          forceArmed
            ? "border-foreground/60 bg-foreground/10 text-foreground hover:bg-foreground/20"
            : "border-primary/50 bg-primary/10 text-primary hover:bg-primary/20",
        )}
      >
        {busy && (
          <span
            aria-hidden
            data-testid="update-progress-fill"
            // A token tint on the button's own token background: it reads as a
            // fill in light and dark alike, with no hardcoded colour.
            className="absolute inset-y-0 left-0 bg-primary/25 transition-[width] duration-300 ease-out"
            style={{ width: `${percent}%` }}
          />
        )}
        <Download
          aria-hidden
          className={cn("relative h-3.5 w-3.5", busy && "animate-pulse")}
        />
        <span className="relative tabular-nums">{label}</span>
        {!busy && !forceArmed && shownVersion && (
          <span className="relative rounded bg-primary/20 px-1 text-[10px] tabular-nums">
            v{shownVersion}
          </span>
        )}
      </button>
      {showNotes && status.notes && (
        <div className="absolute right-0 top-full z-50 mt-1 w-80 rounded-md border border-border bg-background p-3 text-left">
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
