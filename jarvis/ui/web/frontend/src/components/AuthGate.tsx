import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { LockKeyhole } from "lucide-react";
import { useT } from "@/i18n";
import { readCachedAssistantName } from "@/lib/assistantNameCache";
import { BootSplash } from "./BootSplash";

declare global {
  interface Window {
    __JARVIS_TOKEN?: string;
    __JARVIS_BOOT_STARTED_AT?: number;
  }
}

type GateState = "checking" | "locked" | "authorized";
type SplashPhase = "shown" | "exiting" | "gone";
const DESKTOP_TOKEN_WAIT_MS = 300;
/** Shortest time the boot splash stays up, measured from the window opening,
 *  so a warm start still reads as one deliberate reveal rather than a flash. */
const SPLASH_MIN_MS = 1100;
/** Time the progress line gets to visibly complete before the splash leaves. */
const SPLASH_COMPLETE_MS = 380;
/** Removes the splash even when `animationend` never fires (a hidden window
 *  throttles animations, and a test environment has none at all). */
const SPLASH_EXIT_FALLBACK_MS = 1000;

interface AuthGateProps {
  children: ReactNode;
}

async function createSession(body: { control_key: string } | { session_token: string }) {
  return fetch("/api/ui/session", {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function readInjectedToken(): string {
  return window.__JARVIS_TOKEN?.trim() ?? "";
}

function waitForInjectedToken(): Promise<string> {
  const existing = readInjectedToken();
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      window.removeEventListener("jarvis-token-ready", onReady);
      resolve(readInjectedToken());
    };
    const onReady = () => finish();
    const timer = window.setTimeout(finish, DESKTOP_TOKEN_WAIT_MS);
    window.addEventListener("jarvis-token-ready", onReady);
  });
}

export function AuthGate({ children }: AuthGateProps) {
  const t = useT();
  const started = useRef(false);
  // The HTML splash starts the reveal before React loads. Continue that same
  // animation phase when the checking gate replaces its DOM.
  const bootShift = useRef(
    `-${Math.max(0, (performance.now() - (window.__JARVIS_BOOT_STARTED_AT ?? performance.now())) / 1000)}s`,
  );
  const [state, setState] = useState<GateState>("checking");
  const [backendWarming, setBackendWarming] = useState(false);
  const [controlKey, setControlKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [splash, setSplash] = useState<SplashPhase>("shown");

  // Once the gate has an answer, the splash stays on top while the app mounts
  // underneath, then leaves with its exit animation — the app is revealed,
  // never swapped in. Two frames after the minimum time, so the first paint of
  // the app has happened before the splash starts to go.
  useEffect(() => {
    if (state === "checking" || splash !== "shown") return;
    const openedAt = window.__JARVIS_BOOT_STARTED_AT ?? 0;
    const wait = Math.max(
      SPLASH_COMPLETE_MS,
      SPLASH_MIN_MS - (performance.now() - openedAt),
    );
    let frame = 0;
    const timer = window.setTimeout(() => {
      frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => setSplash("exiting"));
      });
    }, wait);
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
    };
  }, [state, splash]);

  useEffect(() => {
    if (splash !== "exiting") return;
    const timer = window.setTimeout(() => setSplash("gone"), SPLASH_EXIT_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [splash]);

  // While the access probe is pending, poll the (always instantly answered)
  // health endpoint. During a cold boot the serve-first bootstrap HOLDS every
  // /api/* request until the real backend registers — which can take a while —
  // so without this the gate shows "Checking access…" for the entire warm-up
  // and the boot looks stuck on an access check that is actually fine. Health
  // answers `warming: true` from the first millisecond, letting the gate show
  // an honest "starting up" instead. Polling starts after a beat so the normal
  // already-warm path (config answers in milliseconds) never pays a request.
  useEffect(() => {
    if (state !== "checking") return;
    let cancelled = false;
    const probe = async () => {
      try {
        const res = await fetch("/api/health", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as { warming?: boolean };
        if (!cancelled) setBackendWarming(body?.warming === true);
      } catch {
        // Offline/unreachable — the /api/config probe owns that outcome.
      }
    };
    const timer = window.setInterval(() => void probe(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [state]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    void (async () => {
      // During a cold boot the serve-first bootstrap holds this request for up
      // to 120 s. One untimed fetch pinned a connection for that whole window;
      // short timed attempts hold at most one connection at a time and pick up
      // the real answer within seconds of the app becoming ready.
      const ATTEMPT_TIMEOUT_MS = 10_000;
      const MAX_ATTEMPTS = 15;
      let response: Response | null = null;
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        try {
          response = await fetch("/api/config", {
            cache: "no-store",
            credentials: "same-origin",
            signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
          });
          break;
        } catch (exc) {
          if (exc instanceof DOMException && exc.name === "TimeoutError") {
            continue; // still warming — ask again with a fresh connection
          }
          // Authentication is required only when the backend explicitly
          // returns 401. Let the existing application surfaces handle
          // warmup/offline failures instead of trapping the user behind an
          // unrelated gate.
          setState("authorized");
          return;
        }
      }
      if (response === null || response.status !== 401) {
        setState("authorized");
        return;
      }

      try {
        const injectedToken = await waitForInjectedToken();
        if (injectedToken) {
          // The WebView credential is single-use. Remove the JavaScript copy
          // before the network round-trip, including failure/restart paths.
          window.__JARVIS_TOKEN = undefined;
          const session = await createSession({ session_token: injectedToken });
          if (session.ok) {
            setState("authorized");
            return;
          }
        }
      } catch {
        // A stale injected token must never bypass the explicit 401 gate.
      }
      setState("locked");
    })();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = controlKey.trim();
    if (!value || submitting) return;

    setSubmitting(true);
    setErrorKey(null);
    try {
      const response = await createSession({ control_key: value });
      if (!response.ok) {
        setErrorKey(
          response.status === 401 ? "auth_gate.invalid" : "auth_gate.unavailable",
        );
        return;
      }
      setControlKey("");
      setState("authorized");
    } catch {
      setErrorKey("auth_gate.unavailable");
    } finally {
      setSubmitting(false);
    }
  };

  const statusLabel = t(backendWarming ? "auth_gate.starting" : "auth_gate.checking");
  // The progress line follows real milestones. The bundle running is 55 %;
  // a warming backend approaches 90 % slowly (it can take a while and must
  // never look stalled or finished); an answer from the gate completes it.
  const [progress, settle] =
    state !== "checking" ? [1, 0.35] : backendWarming ? [0.9, 25] : [0.55, 0.9];
  // The wrapper is not decoration: the blank-window watchdog in index.html
  // treats a bare splash in #root as "bundle still loading" and reloads it
  // after its grace period. A held cold boot can legitimately outlast that,
  // so the gate's splash must count as the app being up.
  const splashLayer =
    splash === "gone" ? null : (
      <main id="jarvis-auth-splash">
        <BootSplash
          name={readCachedAssistantName("")}
          status={statusLabel}
          shift={bootShift.current}
          progress={progress}
          settle={settle}
          exiting={splash === "exiting"}
          onExited={() => setSplash("gone")}
        />
      </main>
    );

  if (state === "checking") return splashLayer;

  if (state === "authorized") {
    return (
      <>
        {children}
        {splashLayer}
      </>
    );
  }

  return (
    <>
      <main className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
        <form
          className="w-full max-w-sm rounded-xl border border-border bg-card p-6"
          onSubmit={submit}
        >
          <div className="mb-5 flex items-start gap-3">
            <div className="rounded-lg bg-primary/10 p-2 text-primary">
              <LockKeyhole className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-base font-semibold">{t("auth_gate.title")}</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("auth_gate.subtitle")}
              </p>
            </div>
          </div>

          <label className="mb-1.5 block text-sm font-medium" htmlFor="control-key">
            {t("auth_gate.control_key")}
          </label>
          <input
            id="control-key"
            autoComplete="current-password"
            autoFocus
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background focus:ring-2 focus:ring-ring"
            disabled={submitting}
            onChange={(event) => setControlKey(event.target.value)}
            placeholder={t("auth_gate.placeholder")}
            type="password"
            value={controlKey}
          />
          {errorKey && (
            <p className="mt-2 text-sm text-destructive" role="alert">
              {t(errorKey)}
            </p>
          )}
          <button
            className="mt-4 w-full rounded-md bg-foreground/70 px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!controlKey.trim() || submitting}
            type="submit"
          >
            {t(submitting ? "auth_gate.submitting" : "auth_gate.submit")}
          </button>

          <div className="mt-5 border-t border-border pt-4">
            <h2 className="text-xs font-medium">{t("auth_gate.where_title")}</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {t("auth_gate.where_hint")}
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
              {t("auth_gate.where_hint_server")}
            </p>
          </div>
        </form>
      </main>
      {splashLayer}
    </>
  );
}
