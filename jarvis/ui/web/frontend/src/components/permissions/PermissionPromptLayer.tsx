import { CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { useRestartApp } from "@/hooks/useRestartApp";
import { fill, useT, useUiLanguage } from "@/i18n";
import { onSharedReturnToWindow } from "@/lib/focusRefresh";
import {
  FALLBACK_APP_NAME,
  isOutsideAskEpisode,
  promptHeadingKey,
  outsideCopyKeys,
  promptSentence,
} from "@/lib/permissionCopy";
import {
  PERMISSION_CARD_OFFSET_VAR,
  RESOLVED_HOLD_MS,
  cardEpisodes,
  type PromptEpisode,
} from "@/lib/permissionPrompts";
import {
  PermissionApiError,
  fetchPermissionRow,
  openPermissionSettings,
  requestPermission,
  resetPermission,
} from "@/lib/permissionsApi";
import { isReadyState, type PermissionId, type PermissionRow } from "@/lib/permissionSnapshot";
import { cn } from "@/lib/utils";
import { usePermissionsStore } from "@/store/permissions";
import { WRAPPING_ACTION_BUTTON, promptActions, type PromptAction } from "./promptActions";
import { SeeAllPermissionsLink } from "./SeeAllPermissionsLink";

/**
 * The floating card that explains a missing macOS permission where the person
 * is, with one click to the right System Settings pane.
 *
 * Mounted lazily by `PermissionPromptHost`, only while the owner window has an
 * episode that needs the person (origin "user", phase "blocked", not dismissed,
 * and no inline surface of its own for that feature). It never opens for a
 * system dialog macOS shows by itself, and never for a background consumer.
 *
 * Placement: a portal at z-[115] in the same top-right column as the toasts
 * (right-4 top-12): ABOVE the setup spotlight (z-110, whose dim would otherwise
 * make the card unclickable during onboarding) and BELOW the caption bar
 * (z-120). It publishes its height as a CSS variable so the toast column starts
 * beneath it. One card at a time plus "+N more".
 *
 * Accessibility: `role="group"` named by its heading; no autofocus (focus
 * belongs to what the person was doing, a dictation target above all); no global
 * Escape handler (the card is not a modal). A polite live region (mounted for the
 * whole life of the layer, so its text is a CHANGE and gets announced) says the
 * card's heading and sentence when it opens and, for at least 5 seconds, the
 * "Allowed" confirmation. When a button the person reached by keyboard removes
 * the card, focus returns to the element that had it before. Motion only where
 * `prefers-reduced-motion` allows. Colours come from theme tokens only.
 *
 * Polling: none. The backend watcher pushes `PermissionResolved`; the layer
 * re-reads the permission rows once when the card appears and (single-flight,
 * jittered, see lib/focusRefresh) when the person comes back to the window.
 */

type MessageKey =
  | "permissions.prompt.still_off"
  | "permissions.prompt.reset_asked"
  | "permissions.prompt.reset_manual"
  | "permissions.rate_limited"
  | "permissions.action_failed";

/**
 * What the "Allowed" confirmation says. A Computer Use mission that stopped for a
 * permission has already ended (nothing resumes it), so "you can carry on" would
 * be wrong there: the person has to ask again.
 */
function allowedKeyFor(feature: string): string {
  return feature === "computer_use" ? "permissions.prompt.allowed_retry" : "permissions.prompt.allowed";
}

export default function PermissionPromptLayer(): ReactNode {
  const episodes = usePermissionsStore((state) => state.episodes);
  const resolved = usePermissionsStore((state) => state.resolved);
  const inline = usePermissionsStore((state) => state.inline);
  const owner = usePermissionsStore((state) => state.owner);
  const headless = usePermissionsStore((state) => state.snapshot?.headless === true);
  const appName = usePermissionsStore((state) => state.snapshot?.app_identity.app_name ?? "");
  const launchedAsBundle = usePermissionsStore(
    (state) => state.snapshot?.app_identity.launched_as_bundle === true,
  );
  const t = useT();

  const language = useUiLanguage();
  const [cursor, setCursor] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const visible = useMemo(
    () => cardEpisodes({ episodes, resolved }, new Set(Object.keys(inline))),
    [episodes, resolved, inline],
  );
  const top = visible.length > 0 ? visible[cursor % visible.length] : null;

  // A card the person was looking at turns into a short confirmation instead of vanishing.
  const confirmation = resolved.find(
    (note) => note.granted && note.hadCard && now - note.ts < RESOLVED_HOLD_MS,
  );
  useEffect(() => {
    if (!confirmation) return undefined;
    const wait = confirmation.ts + RESOLVED_HOLD_MS - Date.now();
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, wait) + 20);
    return () => window.clearTimeout(timer);
  }, [confirmation]);
  useEffect(() => {
    // A newly arrived confirmation must be compared with the current time, not the mount time.
    setNow(Date.now());
  }, [resolved]);

  const column = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = column.current;
    if (!el) return undefined;
    const root = document.documentElement;
    const apply = () => {
      const height = el.getBoundingClientRect().height;
      root.style.setProperty(PERMISSION_CARD_OFFSET_VAR, height > 0 ? `${Math.ceil(height) + 8}px` : "0px");
    };
    apply();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(apply);
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      root.style.removeProperty(PERMISSION_CARD_OFFSET_VAR);
    };
  }, []);

  // Focus belongs to what the person was doing. Remember where it was when it moved
  // into the card (Tab, a click), and give it back when the card goes away under it.
  const focusOrigin = useRef<HTMLElement | null>(null);
  const topKey = top?.key ?? null;
  const hadFocusInside = useRef(false);
  useEffect(() => {
    const active = document.activeElement;
    const inside = column.current?.contains(active) === true;
    if (!inside && hadFocusInside.current) {
      // The focused button was removed with the card: focus fell back to <body>.
      const back = focusOrigin.current;
      if ((active === document.body || active === null) && back?.isConnected) back.focus();
      focusOrigin.current = null;
    }
    hadFocusInside.current = inside;
  }, [topKey, confirmation]);

  if (!owner || headless) return null;
  const name = appName || FALLBACK_APP_NAME;
  // What a screen reader hears when the card opens: the heading and the sentence.
  const announcement = top
    ? `${t(promptHeadingKey(top))}. ${promptSentence({ t, language, episode: top, appName: name, launchedAsBundle })}`
    : confirmation
      ? t(allowedKeyFor(confirmation.feature))
      : "";

  return createPortal(
    <div
      ref={column}
      data-testid="permission-prompt-layer"
      // z-[115]: above the setup spotlight (z-110), below the caption bar (z-120).
      // Same top-right column as the toasts; top-12 keeps it under the caption.
      className="pointer-events-none fixed right-4 top-12 z-[115] flex w-[320px] flex-col gap-2"
      onFocusCapture={(event) => {
        hadFocusInside.current = true;
        const from = event.relatedTarget;
        if (from instanceof HTMLElement && !event.currentTarget.contains(from)) {
          focusOrigin.current = from;
        }
      }}
      onBlurCapture={(event) => {
        // Focus moved to another element on purpose: nothing to give back later.
        // (A button removed with its card blurs to nothing: relatedTarget is null.)
        const to = event.relatedTarget;
        if (to instanceof HTMLElement && !event.currentTarget.contains(to)) {
          hadFocusInside.current = false;
        }
      }}
    >
      <div role="status" aria-live="polite" className="sr-only" data-testid="permission-live-region">
        {announcement}
      </div>
      {top ? (
        <PermissionPromptCard
          key={top.key}
          episode={top}
          appName={name}
          launchedAsBundle={launchedAsBundle}
          more={visible.length - 1}
          onMore={() => setCursor((value) => value + 1)}
        />
      ) : confirmation ? (
        <div
          data-testid="permission-allowed-card"
          className={cn(
            // shadow-float: the card sits over whatever pane is behind it (a terminal in a
            // light theme is near-white); the ring and shadow keep its edge readable.
            "pointer-events-auto flex items-start gap-3 rounded-lg border border-border bg-card/95 px-3 py-2.5 text-foreground shadow-float backdrop-blur",
            "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-right-4 motion-safe:duration-200",
          )}
        >
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
          <p className="min-w-0 flex-1 text-xs leading-relaxed">{t(allowedKeyFor(confirmation.feature))}</p>
        </div>
      ) : null}
    </div>,
    document.body,
  );
}

function PermissionPromptCard({
  episode,
  appName,
  launchedAsBundle,
  more,
  onMore,
}: {
  episode: PromptEpisode;
  appName: string;
  /** `app_identity.launched_as_bundle`: picks the outside-app wording. */
  launchedAsBundle: boolean;
  more: number;
  onMore: () => void;
}) {
  const t = useT();
  const language = useUiLanguage();
  const dismiss = usePermissionsStore((state) => state.dismiss);
  const restartApp = useRestartApp();

  const [rows, setRows] = useState<Record<string, PermissionRow>>({});
  const [busy, setBusy] = useState<PromptAction | null>(null);
  const [message, setMessage] = useState<MessageKey | null>(null);
  const [returned, setReturned] = useState(false);
  const settingsOpened = useRef(false);
  const inflight = useRef<Promise<Record<string, PermissionRow>> | null>(null);
  // Set on a return to the window, consumed by the next row read: that read
  // carries `?activated=1` so the backend promotes the episode and re-probes.
  const activatedNext = useRef(false);
  const mounted = useRef(true);
  const headingId = `permission-prompt-${episode.key.replace(/[^a-z0-9]+/gi, "-")}`;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const permissionsKey = episode.permissions.join("+");
  /**
   * Single-flight read of the episode's permission rows (cheap `GET /{id}`).
   * `activated` marks the first read after the window regained focus; when a
   * read is already in flight (it started before the hint) a second one follows
   * it, so the hint is never lost to the single-flight join.
   */
  const refreshRows = useCallback((activated = false): Promise<Record<string, PermissionRow>> => {
    if (activated) activatedNext.current = true;
    if (activated && inflight.current) {
      return inflight.current.then(() => refreshRows());
    }
    if (!inflight.current) {
      const ids = permissionsKey.split("+") as PermissionId[];
      const hint = activatedNext.current;
      activatedNext.current = false;
      inflight.current = (async () => {
        const read = await Promise.all(
          ids.map(async (id) => {
            try {
              return await fetchPermissionRow(id, { activated: hint });
            } catch {
              // Offline or a warming backend: keep the last known rows. The
              // buttons do not depend on them, and the next return to the
              // window or "Check again" reads again.
              return null;
            }
          }),
        );
        const next: Record<string, PermissionRow> = {};
        for (const row of read) if (row) next[row.id] = row;
        return next;
      })().finally(() => {
        inflight.current = null;
      });
    }
    return inflight.current.then((next) => {
      if (mounted.current) setRows((previous) => ({ ...previous, ...next }));
      return next;
    });
  }, [permissionsKey]);

  useEffect(() => {
    void refreshRows();
  }, [refreshRows]);

  useEffect(
    () =>
      onSharedReturnToWindow(() => {
        if (settingsOpened.current) setReturned(true);
        void refreshRows(true);
      }),
    [refreshRows],
  );

  const unmet = episode.permissions.filter((id) => {
    const row = rows[id];
    return !row || !isReadyState(row.status);
  });
  const primary = (unmet[0] ?? episode.permissions[0]) as PermissionId;
  const canReset = rows[primary]?.can_reset === true;
  const stillOff = unmet.length > 0 && Object.keys(rows).length > 0;

  const actions = promptActions(episode, {
    returnedFromSettings: returned,
    stillOff,
    canReset,
    restartMayHelp: primary === "screen_recording" || primary === "input_monitoring",
  });
  // Back from Settings, still off, and a restart is offered before a reset: say why.
  const restartMaybe = episode.reason !== "restart_hint" && actions.includes("restart");

  const run = async (action: PromptAction, work: () => Promise<void>) => {
    setBusy(action);
    setMessage(null);
    try {
      await work();
    } catch (exc) {
      if (!mounted.current) return;
      setMessage(
        exc instanceof PermissionApiError && exc.status === 429
          ? "permissions.rate_limited"
          : "permissions.action_failed",
      );
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const ask = (allowOutside: boolean) => async () => {
    await requestPermission(primary, {
      feature: episode.feature,
      target: episode.target || undefined,
      allow_outside_app: allowOutside,
    });
    await refreshRows();
  };

  const handlers: Record<PromptAction, () => void> = {
    continue: () => void run("continue", ask(false)),
    allow_outside: () => void run("allow_outside", ask(true)),
    open_settings: () =>
      void run("open_settings", async () => {
        await openPermissionSettings(primary);
        settingsOpened.current = true;
      }),
    check_again: () =>
      void run("check_again", async () => {
        const next = await refreshRows();
        const stillMissing = episode.permissions.some((id) => {
          const row = next[id] ?? rows[id];
          return !row || !isReadyState(row.status);
        });
        if (stillMissing) setMessage("permissions.prompt.still_off");
      }),
    reset: () =>
      void run("reset", async () => {
        try {
          const done = await resetPermission(primary);
          const status = done.permission?.status;
          if (status === "not_determined" || status === "not_granted") {
            await requestPermission(primary, {
              feature: episode.feature,
              target: episode.target || undefined,
            });
            setMessage("permissions.prompt.reset_asked");
          } else if (status === "granted") {
            setMessage(null);
          } else {
            setMessage("permissions.prompt.reset_manual");
          }
        } catch (exc) {
          if (exc instanceof PermissionApiError && exc.status === 409) {
            // The backend refused: it is allowed right now (the card is about to
            // resolve by itself) or it cannot be reset from here. Either way the
            // honest next step is the manual path.
            setMessage("permissions.prompt.reset_manual");
            await refreshRows();
            return;
          }
          throw exc;
        }
        await refreshRows();
      }),
    restart: () => void restartApp.restart(),
    not_now: () => dismiss(episode.key),
  };

  const label = (action: PromptAction): string => {
    switch (action) {
      case "continue":
        return t("permissions.prompt.action.continue");
      case "allow_outside":
        return fill(t(outsideCopyKeys(launchedAsBundle).action), { app: appName });
      case "open_settings":
        return t("permissions.prompt.action.open_settings");
      case "check_again":
        return t("permissions.prompt.action.check_again");
      case "reset":
        return t("permissions.prompt.action.reset");
      case "restart":
        return restartApp.restarting || restartApp.forceArmed
          ? restartApp.buttonLabel
          : t("permissions.prompt.action.restart");
      case "not_now":
        return t("permissions.prompt.action.not_now");
    }
  };

  // Name only what is still missing; once every row reads granted (the resolve
  // event is on its way) the episode's own list keeps the sentence whole.
  const sentence = promptSentence({
    t,
    language,
    episode,
    appName,
    missing: unmet.length > 0 ? unmet : undefined,
    launchedAsBundle,
  });
  const pathKey = `permissions.items.${primary}.path`;
  const path = t(pathKey);
  const showPath =
    path !== pathKey && (episode.reason === "denied" || episode.reason === "needs_settings");
  const primaryAction = actions[0] !== "not_now" ? actions[0] : null;

  return (
    <div
      role="group"
      aria-labelledby={headingId}
      data-testid="permission-prompt-card"
      data-reason={episode.reason}
      data-feature={episode.feature}
      className={cn(
        // shadow-float: see the confirmation card above.
        "pointer-events-auto rounded-lg border border-border bg-card/95 px-3 py-2.5 text-foreground shadow-float backdrop-blur",
        "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-right-4 motion-safe:duration-200",
      )}
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1 text-xs leading-relaxed">
          <h2 id={headingId} className="text-sm font-medium text-foreground-strong">
            {t(promptHeadingKey(episode))}
          </h2>
          <p className="mt-1 break-words" data-testid="permission-prompt-sentence">
            {sentence}
          </p>

          {episode.permissions.length > 1 && (
            <ol
              aria-label={t("permissions.prompt.steps_label")}
              className="mt-2 space-y-1"
              data-testid="permission-prompt-steps"
            >
              {episode.permissions.map((id) => {
                const row = rows[id];
                const ready = row ? isReadyState(row.status) : false;
                return (
                  // The label wraps rather than truncates: a status pill can be wide in
                  // some languages and the permission name must stay readable beside it.
                  <li key={id} className="flex items-start justify-between gap-2">
                    <span className="min-w-0 flex-1 break-words">{t(`permissions.items.${id}.title`)}</span>
                    {row && (
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2 py-0.5 text-micro font-medium",
                          ready
                            ? "bg-muted-foreground/10 text-muted-foreground"
                            : "bg-secondary text-foreground",
                        )}
                      >
                        {t(`permissions.status.${row.status}`)}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          {showPath && (
            <p className="mt-1 break-words text-muted-foreground" data-testid="permission-prompt-path">
              {fill(t("permissions.path_label"), { path })}
            </p>
          )}
          {/* The dedicated outside sentence above already names the grantee. */}
          {episode.outside_app && !isOutsideAskEpisode(episode) && (
            <p className="mt-1 break-words text-muted-foreground">
              {fill(t(outsideCopyKeys(launchedAsBundle).note), { app: appName })}
            </p>
          )}
          {restartMaybe && (
            <p className="mt-1 break-words text-foreground" data-testid="permission-prompt-restart-maybe">
              {fill(t("permissions.prompt.restart_maybe"), { app: appName })}
            </p>
          )}
          {message && (
            <p role="status" className="mt-1 break-words text-foreground" data-testid="permission-prompt-message">
              {t(message)}
            </p>
          )}
          {episode.detail && (
            <details className="mt-1 text-muted-foreground">
              <summary className="cursor-pointer select-none">{t("permissions.prompt.details")}</summary>
              <p className="mt-1 break-words">{episode.detail}</p>
            </details>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {actions.map((action) => (
              <Button
                key={action}
                type="button"
                size="sm"
                className={WRAPPING_ACTION_BUTTON}
                variant={action === primaryAction ? "default" : action === "not_now" ? "ghost" : "outline"}
                disabled={busy !== null && action !== "not_now"}
                onClick={handlers[action]}
                data-action={action}
              >
                {busy === action && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />}
                {label(action)}
              </Button>
            ))}
            {more > 0 && (
              <button
                type="button"
                onClick={onMore}
                className="rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                data-testid="permission-prompt-more"
              >
                {fill(t("permissions.prompt.more"), { n: more })}
              </button>
            )}
            <SeeAllPermissionsLink />
          </div>
        </div>
      </div>
    </div>
  );
}
