/**
 * The one thing Personal Jarvis says when a macOS permission stopped a feature:
 * a toast, in the app's existing toast layer, with one sentence and at most one
 * button.
 *
 * macOS shows its OWN dialog the first time a feature needs a permission, and
 * the app draws nothing around it. What macOS does NOT do is say anything after
 * the person pressed "Don't allow" (it never asks twice) or when a granted
 * permission still does not work. That silence is all this module fills: it
 * reads the two bus events `PermissionNeeded` / `PermissionResolved` (typed in
 * `permissionEvents.ts`) and decides whether a toast is due.
 *
 * A toast is due only when ALL of these hold:
 *   - the person caused it (`origin === "user"`), macOS is not asking right now
 *     (`phase === "blocked"`), and this window is the one that owns host-level
 *     prompts (the embedded desktop window on a Mac; never a detached solo
 *     window, a remote browser, or another OS);
 *   - this episode is not already on screen in this window. The key is forgotten
 *     when `PermissionResolved` ends the episode AND as soon as its toast is gone
 *     (dismissed or expired) after a short cooldown, so the next press of the
 *     feature that ends blocked again is told again instead of staying silent
 *     for the rest of the episode;
 *   - there is something the person can do or know. A background consumer never
 *     toasts, with ONE exception: the wake word is the always-listening feature
 *     the person switched on, so a denied microphone for it is told once per app
 *     session.
 *
 * Pure planning (`planPermissionToast`) is separate from the state and the I/O
 * (`handlePermissionToastEvent`) so the table of sentences and buttons is one
 * readable function with its own test.
 */
import { translate, fill } from "@/i18n";
import { isEmbeddedMacWindow } from "@/lib/embeddedDesktop";
import {
  PERMISSION_FEATURES,
  PERMISSION_NEEDED_ORIGINS,
  PERMISSION_NEEDED_PHASES,
  PERMISSION_NEEDED_REASONS,
} from "@/lib/permissionEvents";
import { useEventStore, type ToastAction } from "@/store/events";

/** How long the toast stays: long enough to read one sentence and aim at its button. */
export const PERMISSION_TOAST_TTL_MS = 20_000;

/**
 * How long a told episode swallows an identical edge even when its toast is already
 * gone: enough to absorb a burst (two sockets, a double press), short enough that the
 * person's next deliberate press is answered.
 */
export const PERMISSION_TOAST_REPEAT_COOLDOWN_MS = 3_000;

/** The permission families that have their own sentence; anything else gets the generic one. */
const FAMILIES_WITH_COPY = [
  "microphone",
  "screen_recording",
  "accessibility",
  "input_monitoring",
  "automation",
] as const;

export type PermissionToastVariant = "needs" | "restricted" | "restart" | "wake_word";

/** What the one button does. */
export type PermissionToastActionKind = "open_settings" | "ask_now" | "ask_outside" | "restart";

/** Everything the toast is made of, before any state or I/O. */
export interface PermissionToastPlan {
  /** Dedupe key: one episode in one situation (feature, permissions, reason). */
  key: string;
  /** The episode id the key belongs to (feature + permissions); `PermissionResolved` ends it. */
  episode: string;
  variant: PermissionToastVariant;
  /** The `PermissionId` of the pane the button works on (`accessibility` for `event_posting`). */
  permission: string;
  /** The `PERMISSION_FEATURES` token the ask is attributed to, or "". */
  feature: string;
  /** Automation only: the player's bundle id the episode is about, else "". */
  target: string;
  /** i18n key of the sentence. */
  messageKey: string;
  /** i18n key of the permission's name, for the sentences that name it. */
  nameKey: string | null;
  action: PermissionToastActionKind | null;
  kind: "info" | "warning";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function oneOf<T extends string>(allowed: readonly T[], value: unknown): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null;
}

/** `event_posting` is an alias of `accessibility`: one pane. */
function paneFamily(permission: string): string {
  return permission === "event_posting" ? "accessibility" : permission;
}

function families(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string" || entry === "") continue;
    const family = paneFamily(entry);
    if (!out.includes(family)) out.push(family);
  }
  return out;
}

/** The id of one episode, as the backend keys it: the feature and its sorted pane families. */
function episodeId(feature: string, permissions: readonly string[]): string {
  return `${feature}:${[...permissions].sort().join("+")}`;
}

/** The sentence key for one permission family (generic for a family without its own copy). */
function sentenceKey(family: string): string {
  return (FAMILIES_WITH_COPY as readonly string[]).includes(family)
    ? `permissions.toast.${family}`
    : "permissions.toast.generic";
}

function nameKey(family: string): string {
  return (FAMILIES_WITH_COPY as readonly string[]).includes(family)
    ? `permissions.toast.name.${family}`
    : "permissions.toast.name.generic";
}

/**
 * Decide what a `PermissionNeeded` payload is worth, or null for "say nothing".
 * Pure: no window, no store, no clock.
 *
 * | situation                                | sentence             | button            |
 * |------------------------------------------|----------------------|-------------------|
 * | denied / needs_settings                  | one per permission   | Open System Settings |
 * | not yet asked, macOS can still ask       | one per permission   | Ask macOS now     |
 * | not yet asked, outside the installed app | outside variant      | Ask macOS now (allow_outside_app) |
 * | restart_hint (allowed, but use failed)   | restart variant      | Quit and reopen   |
 * | restricted (profile / parental control)  | restricted variant   | none              |
 * | unavailable (no desktop session)         | nothing to tell      | -                 |
 */
export function planPermissionToast(
  payload: unknown,
  options: { wakeWordToldThisSession?: boolean } = {},
): PermissionToastPlan | null {
  if (!isRecord(payload)) return null;
  const feature = typeof payload.feature === "string" ? payload.feature : "";
  const permissions = families(payload.permissions);
  const reason = oneOf(PERMISSION_NEEDED_REASONS, payload.reason);
  const phase = oneOf(PERMISSION_NEEDED_PHASES, payload.phase);
  const origin = oneOf(PERMISSION_NEEDED_ORIGINS, payload.origin);
  if (!feature || permissions.length === 0 || !reason || !phase || !origin) return null;
  // macOS is showing its own dialog: the app adds nothing to it.
  if (phase !== "blocked") return null;

  const wakeWord = origin !== "user" && feature === "wake_word" && reason === "denied";
  if (origin !== "user" && !(wakeWord && !options.wakeWordToldThisSession)) return null;
  // Nothing to tell and nothing to do (no desktop session to ask in).
  if (reason === "unavailable") return null;

  const canPrompt = payload.can_prompt === true;
  const canOpenSettings = payload.can_open_settings === true;
  const outsideApp = payload.outside_app === true;
  const permission = permissions[0];
  const knownFeature = (PERMISSION_FEATURES as readonly string[]).includes(feature) ? feature : "";
  const base = {
    key: `${episodeId(feature, permissions)}:${reason}`,
    episode: episodeId(feature, permissions),
    permission,
    feature: knownFeature,
    target: permission === "automation" && typeof payload.target === "string" ? payload.target : "",
    nameKey: nameKey(permission),
  };

  if (wakeWord) {
    return {
      ...base,
      variant: "wake_word",
      messageKey: "permissions.toast.wake_word",
      nameKey: null,
      action: canOpenSettings ? "open_settings" : null,
      kind: "warning",
    };
  }
  if (reason === "restricted") {
    return {
      ...base,
      variant: "restricted",
      messageKey: "permissions.toast.restricted",
      action: null,
      kind: "info",
    };
  }
  if (reason === "restart_hint") {
    return {
      ...base,
      variant: "restart",
      messageKey: "permissions.toast.restart",
      action: "restart",
      kind: "warning",
    };
  }
  // denied / needs_settings / not_determined: the person has to act.
  if (canPrompt) {
    return {
      ...base,
      variant: "needs",
      messageKey: outsideApp ? "permissions.toast.outside" : sentenceKey(permission),
      action: outsideApp ? "ask_outside" : "ask_now",
      kind: "info",
    };
  }
  return {
    ...base,
    variant: "needs",
    messageKey: sentenceKey(permission),
    action: canOpenSettings ? "open_settings" : null,
    kind: "warning",
  };
}

// ---------------------------------------------------------------------------
// State and I/O
// ---------------------------------------------------------------------------

/**
 * Whether THIS window owns host-level permission toasts. Every window has its own
 * socket and the server broadcasts to all of them, so without an owner N windows
 * would toast N times. The owner is the main window of the embedded desktop app on
 * macOS; a detached solo window is one section and never is, and a remote browser
 * must never offer "Open System Settings" for another computer. Read at event time:
 * the desktop shell injects the bridge flag AFTER the page loads.
 */
export function isPermissionToastWindow(): boolean {
  return !useEventStore.getState().solo && isEmbeddedMacWindow();
}

/** Episode key -> the message that was shown, so a grant can take the toast down again. */
const told = new Map<string, { episode: string; message: string; at: number }>();
/** Episode keys the snapshot seed already handled in this page load. */
const seeded = new Set<string>();
let wakeWordTold = false;

/** Tests only: forget what this window already said. */
export function resetPermissionToastState(): void {
  told.clear();
  seeded.clear();
  wakeWordTold = false;
}

/** The pieces that need React state (the restart hook), handed in by `usePermissionToast`. */
export interface PermissionToastDeps {
  /**
   * Quit and reopen (`useRestartApp`). Says "missions are running, press again" and
   * "could not restart" (`permissions.restart_failed`) itself.
   */
  restart: () => Promise<void>;
}

async function post(url: string, body?: Record<string, unknown>): Promise<void> {
  const res = await fetch(
    url,
    body === undefined
      ? { method: "POST" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

function failedToast(error: unknown): void {
  // The person pressed a button and nothing happened: say so, and keep the cause for support.
  console.warn("Permission toast action failed:", error);
  useEventStore.getState().pushToast("error", translate("permissions.toast.action_failed"));
}

function actionFor(plan: PermissionToastPlan, deps: PermissionToastDeps): ToastAction | undefined {
  switch (plan.action) {
    case null:
      return undefined;
    case "open_settings":
      return {
        label: translate("permissions.toast.action.open_settings"),
        onAction: async () => {
          try {
            await post(`/api/permissions/${plan.permission}/open-settings?dry_run=false`);
          } catch (error) {
            failedToast(error);
          }
        },
      };
    case "ask_now":
    case "ask_outside":
      return {
        label: translate("permissions.toast.action.ask_now"),
        onAction: async () => {
          const body: Record<string, unknown> = {};
          if (plan.feature) body.feature = plan.feature;
          if (plan.target) body.target = plan.target;
          // Only the person's own click may confirm that macOS records the grant for
          // the app that started Jarvis; the route refuses it from an agent.
          if (plan.action === "ask_outside") body.allow_outside_app = true;
          try {
            await post(
              `/api/permissions/${plan.permission}/request?dry_run=false`,
              Object.keys(body).length > 0 ? body : undefined,
            );
          } catch (error) {
            failedToast(error);
          }
        },
      };
    case "restart":
      return {
        label: translate("permissions.toast.action.restart"),
        // A restart that answers "missions are running" needs a second press: stay.
        keepOpen: true,
        onAction: () => deps.restart(),
      };
  }
}

function messageFor(plan: PermissionToastPlan): string {
  const template = translate(plan.messageKey);
  return plan.nameKey ? fill(template, { what: translate(plan.nameKey) }) : template;
}

/**
 * Whether this key's toast is still being told: on screen right now, or shown
 * within the repeat cooldown. A toast that was dismissed or expired is not: the
 * person is trying the feature again and is owed the sentence again.
 */
function isStillTold(key: string): boolean {
  const entry = told.get(key);
  if (!entry) return false;
  if (Date.now() - entry.at < PERMISSION_TOAST_REPEAT_COOLDOWN_MS) return true;
  return useEventStore.getState().toasts.some((toast) => toast.message === entry.message);
}

function forgetEpisode(feature: string, permissions: readonly string[], granted: boolean): void {
  const store = useEventStore.getState();
  for (const [key, entry] of [...told]) {
    const [entryFeature, entrySet] = entry.episode.split(":");
    if (entryFeature !== feature) continue;
    const overlaps =
      permissions.length === 0 || entrySet.split("+").some((family) => permissions.includes(family));
    if (!overlaps) continue;
    told.delete(key);
    // The permission was granted: the sentence that said it was missing is stale.
    if (granted) {
      for (const toast of store.toasts) {
        if (toast.message === entry.message) store.dismissToast(toast.id);
      }
    }
  }
}

/**
 * Feed one bus event in. Cheap for every event but the two it reads, so the
 * WebSocket hook can call it for each envelope.
 */
export function handlePermissionToastEvent(
  eventName: string,
  payload: unknown,
  deps: PermissionToastDeps,
): void {
  if (eventName === "PermissionResolved") {
    if (!isRecord(payload)) return;
    // Shape: `PermissionResolvedPayload` (permissionEvents.ts); read defensively.
    const feature = typeof payload.feature === "string" ? payload.feature : "";
    if (feature === "") return;
    forgetEpisode(feature, families(payload.permissions), payload.granted === true);
    return;
  }
  if (eventName !== "PermissionNeeded") return;
  if (!isPermissionToastWindow()) return;
  // Shape: `PermissionNeededPayload` (permissionEvents.ts); read defensively.
  announce(payload, deps);
}

/** Plan one episode and, when it is due and not already told, put its toast up. */
function announce(payload: unknown, deps: PermissionToastDeps): void {
  const plan = planPermissionToast(payload, {
    wakeWordToldThisSession: wakeWordTold,
  });
  if (!plan || isStillTold(plan.key)) return;

  const message = messageFor(plan);
  told.set(plan.key, { episode: plan.episode, message, at: Date.now() });
  if (plan.variant === "wake_word") wakeWordTold = true;
  useEventStore.getState().pushToast(plan.kind, message, {
    action: actionFor(plan, deps),
    ttlMs: PERMISSION_TOAST_TTL_MS,
  });
}

/**
 * Catch up on the episodes that opened while no window was listening (autostart
 * with the window hidden, a wake-word denial at boot before the socket was open):
 * the bus event was published to nobody and is never repeated. Reads the open
 * episodes from the read-only status route (it never asks macOS for anything) and
 * runs each through the same plan as a live event. Owner window only. Each key is
 * seeded once per page load, so a socket that reconnects does not repeat a toast
 * the person already saw and let expire.
 */
export async function seedPermissionToasts(deps: PermissionToastDeps): Promise<void> {
  if (!isPermissionToastWindow()) return;
  let needed: unknown;
  try {
    const res = await fetch("/api/permissions/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    needed = ((await res.json()) as { needed?: unknown }).needed;
  } catch (error) {
    // Offline or still booting: the live events keep working; the seed is a bonus.
    console.debug("Permission toast seed skipped:", error);
    return;
  }
  if (!Array.isArray(needed)) return;
  // The window may have changed owner state while the request was in flight.
  if (!isPermissionToastWindow()) return;
  for (const entry of needed) {
    const plan = planPermissionToast(entry, { wakeWordToldThisSession: wakeWordTold });
    if (!plan || seeded.has(plan.key)) continue;
    // Seeded even when it was already told live: a reconnect must not repeat it.
    seeded.add(plan.key);
    announce(entry, deps);
  }
}
