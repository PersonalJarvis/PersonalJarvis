import { useCallback, useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";

import { useEventStore } from "@/store/events";
import {
  fetchUpdateProgress,
  useUpdate,
  type UpdateProgress,
} from "@/hooks/useUpdate";
import { clsx } from "clsx";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { SectionNavButtons } from "@/components/layout/SectionNavButtons";
import { IdeLayoutSwitch } from "@/components/agentic/threads/IdeLayoutSwitch";
import { IDE_SECTIONS } from "@/lib/ideSections";
import { IdeCaptionTools, IdeCommandSearch } from "@/components/layout/IdeCaptionTools";
import { useDesktopChrome, WindowControls } from "@/components/layout/WindowControls";

/**
 * The window's title strip, on every screen.
 *
 * It is one thin row at the top of the window: back/forward at the left, the
 * empty middle to drag the window, and minimize, maximize and close at the
 * right. Theme, restart and the sidebar toggles are not caption buttons; the
 * theme lives in Settings. On the Agentic IDE the caption also carries the
 * IDE's own tools: command search and the layout switch in the middle, add
 * agent, voice, git and the side panel at the right end.
 *
 * The update button below (rendered in the sidebar) restarts through
 * ``/api/settings/restart-app``, which spawns a detached relauncher (see
 * jarvis/ui/relauncher.py). On a headless host that endpoint returns 503 and
 * we recover with an honest toast. A restart tears down the whole app
 * mid-task, so it honors the mission guard (409 → arm a force override)
 * rather than killing live missions silently. We avoid a native
 * ``window.confirm`` on purpose — it blocks the pywebview loop.
 */
const CONFIRM_TIMEOUT_MS = 4000;

/**
 * The one recipe every control in this bar follows.
 *
 * The three buttons had drifted into three different shapes — a bordered
 * `bg-secondary/40` pill, a bordered `bg-primary/10` pill in accent ink, and a
 * bordered `bg-foreground/10` pill — none of which is a surface in the system:
 * each was a token multiplied by an opacity, so all three sat at whatever value
 * the ground behind them happened to be. Shape, size, radius and focus ring
 * live here; only the FILL differs, and only because the three states differ.
 *
 * Composed with `clsx`, not `cn`: tailwind-merge reads any unfamiliar `text-*`
 * class as a colour, so `cn("text-body", "text-muted-foreground")` throws the
 * SIZE away and the button falls back to the inherited 16 px. Font size and
 * colour never conflict in real CSS. (The durable fix is teaching
 * tailwind-merge the scale in `lib/utils.ts`; that file is not part of this
 * change.)
 */
const CHROME_BUTTON =
  "inline-flex h-8 shrink-0 items-center gap-2 rounded-md text-base font-medium " +
  "transition-[background-color,color,transform] duration-150 " +
  "motion-safe:active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-default disabled:opacity-70";

/** Resting chrome: no fill of its own, one step up under the pointer. */
const CHROME_QUIET = "text-muted-foreground hover:bg-secondary hover:text-foreground";

/**
 * A control waiting for a second, deliberate click — "confirm restart",
 * "restart anyway". Amber is the product's "needs attention" hue and this is
 * the only place in the shell that earns it: the guard already refused once.
 */
const CHROME_ARMED = "bg-warning text-background";

export function TopBar() {
  const chrome = useDesktopChrome();
  const controls = chrome.frameless ? chrome.controls : "none";

  return (
    // One title strip for the whole window. The empty middle is the drag
    // handle (pywebview only starts a drag on this class, not on a child
    // button — so the buttons sit beside it, never inside it).
    // `pointer-events-auto` keeps the strip live while a modal dialog has set
    // `pointer-events: none` on <body>: the full-window Settings hub leaves
    // the caption visible, and its window controls must still work.
    <div
      data-testid="window-caption"
      className="pointer-events-auto fixed inset-x-0 top-0 z-[120] flex h-8 items-stretch bg-transparent"
    >
      {controls === "leading" && (
        <WindowControls controls={controls} maximized={chrome.maximized} onCommand={chrome.command} />
      )}
      <SectionNavButtons />
      <div
        className="pywebview-drag-region min-w-0 flex-1"
        onDoubleClick={() => {
          if (chrome.frameless) chrome.command("maximize");
        }}
      />
      <IdeCaptionSwitch />
      <div className="flex shrink-0 items-center">
        <CodingModeBadge />
        <IdeCaptionRight />
        {controls === "trailing" && (
          <WindowControls controls={controls} maximized={chrome.maximized} onCommand={chrome.command} />
        )}
      </div>
    </div>
  );
}

/**
 * The Agentic IDE's grid / threads switch, centred in the caption, with the
 * command search to its left. Only on the IDE: everywhere else the caption's
 * middle stays the window's drag handle. Both float over that handle rather
 * than splitting it, so the strip keeps dragging on both sides. The search
 * hangs off the switch's left edge, so the switch stays exactly centred, and
 * it steps aside on a narrow window where it would reach back/forward.
 */
function IdeCaptionSwitch() {
  const onIde = useEventStore((s) => IDE_SECTIONS.includes(s.activeSection));
  if (!onIde) return null;
  return (
    <div className="pointer-events-none absolute inset-y-0 left-1/2 flex -translate-x-1/2 items-center">
      <div className="relative flex items-center">
        <IdeCommandSearch className="pointer-events-auto absolute right-full mr-2 hidden lg:flex" />
        <IdeLayoutSwitch className="pointer-events-auto" />
      </div>
    </div>
  );
}

/** The IDE's tools at the caption's right end, beside the window buttons. */
function IdeCaptionRight() {
  const onIde = useEventStore((s) => IDE_SECTIONS.includes(s.activeSection));
  return onIde ? <IdeCaptionTools /> : null;
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
 * Release notes arrive as GitHub Markdown. The panel shows them as plain
 * prose, so heading hashes, bold markers and list bullets are dropped rather
 * than printed as literal punctuation.
 */
function plainNotes(notes: string): string {
  return notes
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s*[-*+]\s+/, "• ")
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/`([^`]+)`/g, "$1"),
    )
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
    .slice(0, 800);
}

/** Geometry of the progress ring that replaces the icon while an update runs. */
const RING_RADIUS = 6.5;
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;

/**
 * A 16 px ring that fills with the update percentage — the whole progress
 * indicator the title strip needs, at the size of the icon it replaces, so a
 * running update never pushes the neighbouring buttons around.
 */
function ProgressRing({ percent, spinning }: { percent: number; spinning: boolean }) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className={cn("h-4 w-4 -rotate-90", spinning && "animate-spin")}
    >
      <circle
        cx="8"
        cy="8"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2"
        className="stroke-border-strong"
      />
      <circle
        cx="8"
        cy="8"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2"
        strokeLinecap="round"
        className="stroke-accent transition-[stroke-dashoffset] duration-300 ease-out"
        strokeDasharray={RING_LENGTH}
        strokeDashoffset={RING_LENGTH * (1 - clamped / 100)}
      />
    </svg>
  );
}

/**
 * The update entry point — at the foot of the sidebar since 2026-10-01
 * (`placement="sidebar"`), where the Claude app keeps its download icon; it
 * used to sit in the title strip among the window buttons, where nobody saw
 * it. There it wears the signal hue so a waiting update is noticed.
 *
 * At rest it is one more quiet icon among theme, detach and restart: a download
 * glyph with a small accent dot. That is the whole announcement — an update is
 * good news, not an alarm, and most of the time the user is busy with
 * something else. Clicking opens a small panel with the version, the release
 * notes and the one action, so a stray click can never start a restart.
 *
 * It renders only when the backend reports a managed install with a newer
 * published release (``status.update_available``) — or with a
 * staged-but-not-installed transaction (``status.pending_update``) left behind
 * by an earlier attempt. "Update & restart" stages the new code
 * (`POST /api/update/apply`) and then restarts to install it, reusing the same
 * mission-guard (409 → force) flow as the restart button. On a dev tree or
 * manual clone the status is ``managed: false``, so this renders nothing and
 * can never trigger a self-update.
 *
 * While the update runs, the icon becomes a progress ring and the panel (if
 * open) shows the percentage, the server's sub-status and a thin bar.
 */
export function UpdateButton({ placement = "titlebar" }: { placement?: "titlebar" | "sidebar" } = {}) {
  const inSidebar = placement === "sidebar";
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { status, refresh } = useUpdate();
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [forceArmed, setForceArmed] = useState(false);
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [restarting, setRestarting] = useState(false);
  const resetTimer = useRef<number | null>(null);
  const rollbackNotified = useRef<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

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

  // The panel closes on a click outside it or on Escape, like every other
  // floating layer — also mid-update, which is harmless: the ring on the
  // button keeps reporting the progress either way.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && root.contains(event.target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

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

  const idle = !status?.managed || (!status.update_available && !status.pending_update);
  // In the title strip the button only exists while there is something to
  // install. At the foot of the sidebar it always stands, like the Claude
  // app's download icon: quiet with nothing waiting (a click says which
  // version this is and can check now), blue with a dot once an update is in.
  if (idle && !inSidebar) return null;
  if (idle) {
    const managed = Boolean(status?.managed);
    const checkNow = async () => {
      if (checking) return;
      setChecking(true);
      try {
        await refresh(true);
      } finally {
        setChecking(false);
      }
    };
    return (
      <div ref={rootRef} className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={t("topbar.update_up_to_date")}
          title={t("topbar.update_up_to_date")}
          aria-haspopup="dialog"
          aria-expanded={open}
          data-testid="update-button"
          data-state="idle"
          className={clsx(
            "relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            open ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <Download aria-hidden strokeWidth={1.75} className="h-4 w-4" />
        </button>
        {open && (
          <div
            role="dialog"
            aria-label={t("topbar.update_up_to_date")}
            data-testid="update-panel"
            className="absolute bottom-full left-0 z-50 mb-2 w-64 rounded-lg bg-popover p-3 text-left shadow-float"
          >
            <div className="text-meta font-semibold text-foreground-strong">{t("topbar.update_up_to_date")}</div>
            {status?.current && (
              <div className="mt-0.5 text-micro text-muted-foreground">
                {fill(t("topbar.update_current_version"), { version: status.current })}
              </div>
            )}
            {managed ? (
              <button
                type="button"
                onClick={() => void checkNow()}
                disabled={checking}
                data-testid="update-check-now"
                className="mt-3 h-7 rounded-md bg-secondary px-2.5 text-micro font-medium text-foreground transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              >
                {checking ? t("topbar.update_checking") : t("topbar.update_check_now")}
              </button>
            ) : (
              <div className="mt-2 text-micro leading-relaxed text-muted-foreground">
                {t("topbar.update_unmanaged")}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }
  const hasOffer = Boolean(status?.update_available);
  if (!status) return null;
  const shownVersion = status.latest ?? status.pending_update?.version ?? null;

  // The mission guard refused (a quit would kill live missions): surface the
  // count and arm a force override, so the next click is the user's explicit
  // decision — never a silent kill.
  function armForce(count: number) {
    setBusy(false);
    setProgress(null);
    setForceArmed(true);
    setOpen(true);
    clearResetTimer();
    resetTimer.current = window.setTimeout(() => {
      setForceArmed(false);
      resetTimer.current = null;
    }, CONFIRM_TIMEOUT_MS);
    pushToast("warning", `${count} ${t("topbar.restart_missions_running")}`);
  }

  async function run(force: boolean) {
    clearResetTimer();
    setBusy(true);
    setRestarting(false);
    setProgress(null);

    // 1. Stage the new code. The server re-verifies the managed-install guard,
    // so a spoofed client can't force a reset on an unmanaged checkout. This is
    // idempotent — with a transaction already staged it succeeds even offline.
    try {
      const applyRes = await fetch(
        force ? "/api/update/apply?force=true" : "/api/update/apply",
        { method: "POST" },
      );
      const applyBody = (await applyRes.json().catch(() => ({}))) as {
        detail?: unknown;
        restart_required?: boolean;
        quitting?: boolean;
        handover?: string | null;
        deps_warning?: string | null;
        desktop_integration_warning?: string | null;
      };
      const missionGuard = applyBody.detail as
        | { error?: string; missions?: unknown[] }
        | undefined;
      if (applyRes.status === 409 && missionGuard?.error === "missions_running") {
        // A native install quits for the installer, so the server runs the
        // mission guard BEFORE downloading. Same override as the restart.
        armForce(missionGuard.missions?.length ?? 0);
        return;
      }
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
            typeof applyBody.detail === "string"
              ? applyBody.detail
              : `HTTP ${applyRes.status}`,
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
      if (applyBody.restart_required === false && applyBody.quitting === false) {
        // Installed, but nothing could close this app (no desktop window,
        // e.g. a browser-only host): it keeps running the old version until
        // the user restarts it. Say so instead of spinning forever.
        setBusy(false);
        setProgress(null);
        setForceArmed(false);
        pushToast(
          "warning",
          withDetail(t("topbar.update_staged_restart_failed"), applyBody.handover ?? null),
        );
        return;
      }
      if (applyBody.restart_required === false) {
        // A native installer took over and the server is already closing
        // this app; the new version starts by itself once it is gone. A
        // restart on top would race the installer for the program files.
        setRestarting(true);
        pushToast("info", t("topbar.update_restarting"));
        return;
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
          armForce(count);
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

  function onInstall() {
    if (busy) return;
    void run(forceArmed);
  }

  // The bar is driven by the server while it works, then pinned at 100 % for
  // the restart — the one phase nothing can measure, because the thing that
  // would report it is the thing shutting down.
  const percent = restarting ? 100 : (progress?.percent ?? 0);
  const busyLabel = restarting
    ? t("topbar.update_restarting")
    : fill(t("topbar.updating_percent"), { percent });
  const title = hasOffer ? t("topbar.update_available") : t("topbar.update_finish_restart");
  const buttonLabel = busy
    ? busyLabel
    : shownVersion
      ? `${title} · v${shownVersion}`
      : title;
  const actionLabel = forceArmed
    ? t("topbar.restart_force")
    : hasOffer
      ? t("topbar.update_now")
      : t("topbar.update_finish_restart");
  const notes = status.notes ? plainNotes(status.notes) : "";

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={busy ? (progress?.detail ?? busyLabel) : buttonLabel}
        aria-label={buttonLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="update-button"
        // While it runs, the button carries the ARIA role of the progress bar
        // its ring draws — a screen reader announces the same percentage.
        // Outside an update those attributes must be absent, not zeroed, or it
        // reads as a bar stuck at 0 %.
        role={busy ? "progressbar" : undefined}
        aria-valuemin={busy ? 0 : undefined}
        aria-valuemax={busy ? 100 : undefined}
        aria-valuenow={busy ? percent : undefined}
        className={clsx(
          inSidebar
            ? "relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            : clsx(CHROME_BUTTON, "relative w-8 justify-center px-0"),
          open ? "bg-secondary text-foreground" : inSidebar ? "text-accent hover:bg-secondary" : CHROME_QUIET,
        )}
      >
        {busy ? (
          <ProgressRing percent={percent} spinning={restarting} />
        ) : (
          <>
            <Download aria-hidden className="h-4 w-4" />
            {/* The whole announcement: one accent dot, like an unread mark. */}
            <span
              aria-hidden
              data-testid="update-dot"
              className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-accent"
            />
          </>
        )}
      </button>
      {open && (
        // A floating layer: the float surface, the float shadow, and no border
        // of its own — the shadow already carries the rim.
        <div
          role="dialog"
          aria-label={title}
          data-testid="update-panel"
          className={clsx(
            "absolute z-50 w-72 rounded-lg bg-popover p-3 text-left shadow-float",
            inSidebar ? "bottom-full left-0 mb-2" : "right-0 top-full mt-1.5",
          )}
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-meta font-semibold text-foreground-strong">
              {busy ? busyLabel : title}
            </span>
            {shownVersion && (
              <span className="shrink-0 text-micro tabular-nums text-muted-foreground">
                v{shownVersion}
              </span>
            )}
          </div>
          {busy ? (
            <>
              <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-secondary">
                <div
                  aria-hidden
                  data-testid="update-progress-fill"
                  className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                  style={{ width: `${percent}%` }}
                />
              </div>
              {progress?.detail && !restarting && (
                <div className="mt-1.5 truncate text-micro tabular-nums text-muted-foreground">
                  {progress.detail}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="mt-0.5 text-micro text-muted-foreground">
                {fill(t("topbar.update_current_version"), { version: status.current })}
              </div>
              {notes && (
                <div className="mt-2.5 max-h-48 overflow-y-auto whitespace-pre-wrap border-t border-border pt-2.5 text-micro leading-relaxed text-muted-foreground">
                  {notes}
                </div>
              )}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-x-2 gap-y-2">
                <span className="min-w-0 text-micro text-muted-foreground">
                  {t("topbar.update_restart_note")}
                </span>
                <div className="ml-auto flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="h-7 rounded-md px-2.5 text-micro font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {t("topbar.update_later")}
                  </button>
                  <button
                    type="button"
                    onClick={onInstall}
                    data-testid="update-install"
                    className={clsx(
                      "h-7 rounded-md px-2.5 text-micro font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      forceArmed
                        ? CHROME_ARMED
                        : "bg-primary text-primary-foreground hover:bg-primary/90",
                    )}
                  >
                    {actionLabel}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
